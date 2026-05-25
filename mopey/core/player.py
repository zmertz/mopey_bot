"""
GuildPlayer — owns all playback state for a single guild.

One instance per guild, created on demand and stored in the MusicCog.
Commands delegate to this class rather than manipulating raw dicts.

Responsibilities:
  - Voice client lifecycle (connect / disconnect)
  - Queue management (delegates to SongQueue)
  - Playback (play, pause, resume, stop, skip, seek)
  - Tracking current song and start time
  - Inactivity timeout
"""

import asyncio
from time import time
from typing import Optional

import discord

from .queue import SongQueue
from .song import Song
from .sources import AudioSource

INACTIVITY_LIMIT = 600  # seconds (10 minutes)

# Before-input options (passed to FFmpeg before the -i flag):
# - reconnect flags handle dropped HTTP streams
# - probesize/analyzeduration are kept small so FFmpeg doesn't block the
#   event loop long at song start doing format detection
_FFMPEG_BEFORE = (
    "-reconnect 1 "
    "-reconnect_streamed 1 "
    "-reconnect_delay_max 5 "
    "-probesize 32768 "        # 32 KB — much smaller than the old 5 MB default
    "-analyzeduration 0"       # skip duration analysis entirely; we already know it
)

# Output options:
# - vn: no video
# - af: volume + aresample with async mode — this is the key fix for speed-ups.
#   aresample=async=1 tells FFmpeg to insert/drop samples to correct for clock
#   drift rather than speeding up or slowing down the audio stream.
_FFMPEG_AFTER = (
    "-vn "
    '-af "volume=0.25,aresample=48000:async=1:first_pts=0"'
)

FFMPEG_OPTIONS = {
    "before_options": _FFMPEG_BEFORE,
    "options": _FFMPEG_AFTER,
}


def _ffmpeg_options_with_seek(position: float) -> dict:
    return {
        "before_options": (
            f"-reconnect 1 -reconnect_streamed 1 "
            f"-reconnect_delay_max 5 "
            f"-probesize 32768 "
            f"-analyzeduration 0 "
            f"-ss {position}"
        ),
        "options": _FFMPEG_AFTER,
    }


class GuildPlayer:

    def __init__(self, guild_id: int, bot: discord.ext.commands.Bot):
        self.guild_id = guild_id
        self.bot = bot

        self.queue = SongQueue()
        self.current_song: Optional[Song] = None
        self.start_time: float = 0.0
        self._seek_position: float = 0.0  # playback position at the time of last seek/play

        self._voice_client: Optional[discord.VoiceClient] = None
        self._last_activity: float = time()
        self._last_channel: Optional[discord.TextChannel] = None
        self._stopping: bool = False  # True when stop() should NOT advance the queue
        self._seeking: bool = False   # True when seek() is mid stop/restart cycle

    # ------------------------------------------------------------------
    # Voice connection
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._voice_client is not None and self._voice_client.is_connected()

    @property
    def is_playing(self) -> bool:
        return self._voice_client is not None and self._voice_client.is_playing()

    @property
    def is_paused(self) -> bool:
        return self._voice_client is not None and self._voice_client.is_paused()

    async def connect(self, channel: discord.VoiceChannel) -> None:
        if self.is_connected:
            return
        self._voice_client = await channel.connect()

    async def disconnect(self) -> None:
        self._stopping = True
        if self._voice_client:
            self._voice_client.stop()
            await self._voice_client.disconnect()
            self._voice_client = None
        self.current_song = None

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    async def play_song(self, song: Song, source: AudioSource, after_ctx) -> None:
        """
        Resolve the song's stream URL and begin playback.
        `after_ctx` is the discord Context used to chain play_next callbacks.
        """
        self._last_activity = time()

        # Always re-resolve to get a fresh stream URL (important for YouTube)
        resolved = await source.resolve(song)

        # Set state BEFORE starting playback so .playing never sees a None
        # window between voice_client.play() and the assignment below.
        self.current_song = resolved
        self.start_time = time()
        self._seek_position = 0.0

        # Create the FFmpegOpusAudio in a thread — the constructor spawns a
        # subprocess and can block the event loop briefly, which causes the
        # initial speed-up burst at the start of every song.
        loop = asyncio.get_event_loop()
        audio = await loop.run_in_executor(
            None,
            lambda: discord.FFmpegOpusAudio(resolved.url, **FFMPEG_OPTIONS)
        )

        self._voice_client.play(
            audio,
            after=lambda e: asyncio.run_coroutine_threadsafe(
                self._after_play(after_ctx, source), self.bot.loop
            )
        )

    async def _after_play(self, ctx, source: AudioSource) -> None:
        """Called automatically when a song finishes. Plays next or clears state."""
        if self._stopping:
            self._stopping = False
            self.current_song = None
            return

        if self._seeking:
            # seek() will install a new after= handler — nothing to do here
            self._seeking = False
            return

        next_song = self.queue.pop_next()
        if next_song:
            # Set current_song immediately so .playing works during resolve()
            self.current_song = next_song
            await self.play_song(next_song, source, ctx)
            # Notify the channel about what's now playing
            if self._last_channel:
                from ..ui.now_playing import send_now_playing
                await send_now_playing(self._last_channel, self, self.bot)
        else:
            self.current_song = None

    def pause(self) -> bool:
        """Pause playback. Returns True if successful."""
        if self.is_playing:
            self._voice_client.pause()
            return True
        return False

    def resume(self) -> bool:
        """Resume playback. Returns True if successful."""
        if self.is_paused:
            self._voice_client.resume()
            return True
        return False

    def stop(self) -> None:
        """Stop playback without advancing the queue (used for hard stop and disconnect)."""
        self._stopping = True
        if self._voice_client:
            self._voice_client.stop()
        self.current_song = None

    async def skip(self) -> bool:
        """
        Skip the current song. The after-callback handles playing the next one.
        Returns True if there was something to skip.
        """
        if not self.is_playing:
            return False
        self._voice_client.stop()  # triggers _after_play via the after= callback
        return True

    async def seek(self, seconds: int, source: AudioSource, ctx) -> Optional[float]:
        """
        Seek forward/backward by `seconds` relative to current position.
        Works while playing or paused.
        Returns the new position in seconds, or None if seek isn't possible.
        """
        if (not self.is_playing and not self.is_paused) or not self.current_song:
            return None

        was_paused = self.is_paused

        # Can't call _voice_client.stop() while paused without it behaving oddly,
        # so resume first, then immediately suppress the callback and stop.
        if was_paused:
            self._voice_client.resume()

        # Current position = where we were when we last seeked/started + time since then
        current_position = self._seek_position + (time() - self.start_time)
        new_position = max(0.0, current_position + seconds)

        if new_position >= self.current_song.duration:
            return None

        # Set _seeking before stop() so _after_play ignores this callback.
        # _after_play clears the flag itself — we never unset it here,
        # because the after= callback fires on a background thread with no
        # guaranteed timing relative to the lines below.
        self._seeking = True
        self._voice_client.stop()

        options = _ffmpeg_options_with_seek(new_position)
        loop = asyncio.get_event_loop()
        audio = await loop.run_in_executor(
            None,
            lambda: discord.FFmpegOpusAudio(self.current_song.url, **options)
        )
        self._voice_client.play(
            audio,
            after=lambda e: asyncio.run_coroutine_threadsafe(
                self._after_play(ctx, source), self.bot.loop
            )
        )
        self.start_time = time()
        self._seek_position = new_position

        # If we were paused before the seek, re-pause immediately
        if was_paused:
            self._voice_client.pause()

        return new_position

    # ------------------------------------------------------------------
    # State inspection
    # ------------------------------------------------------------------

    @property
    def elapsed(self) -> int:
        """Seconds elapsed in the current song."""
        if not self.current_song:
            return 0
        position = self._seek_position + (time() - self.start_time)
        return max(0, min(int(position), self.current_song.duration))

    # ------------------------------------------------------------------
    # Inactivity
    # ------------------------------------------------------------------

    def update_activity(self, channel: discord.TextChannel) -> None:
        self._last_activity = time()
        self._last_channel = channel

    async def check_inactivity(self) -> bool:
        """
        Check if the bot has been inactive past the limit and disconnect if so.
        Returns True if it disconnected.
        """
        if self.is_playing:
            self._last_activity = time()
            return False

        if time() - self._last_activity > INACTIVITY_LIMIT:
            if self.is_connected:
                await self.disconnect()
                if self._last_channel:
                    await self._last_channel.send("Disconnected due to inactivity.")
            return True

        return False