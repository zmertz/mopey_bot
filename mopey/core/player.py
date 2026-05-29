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
from ..utils.log import get_logger

log = get_logger(__name__)

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
    '-af "volume=0.25,aresample=48000:async=1000:first_pts=0" '
    "-bufsize 512k"
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

        # Prefetch: resolved song + ready-to-play audio object for the next queued song.
        # Populated in the background while the current song is playing so the
        # transition between songs doesn't block the event loop.
        self._prefetched_song: Optional[Song] = None
        self._prefetched_audio: Optional[discord.FFmpegOpusAudio] = None
        self._prefetch_task: Optional[asyncio.Task] = None

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
        log.info(f"[guild={self.guild_id}] Connected to voice channel: #{channel.name}")

    async def disconnect(self) -> None:
        self._stopping = True
        self._clear_prefetch()
        if self._voice_client:
            channel = self._voice_client.channel.name if self._voice_client.channel else "unknown"
            self._voice_client.stop()
            await self._voice_client.disconnect()
            self._voice_client = None
            log.info(f"[guild={self.guild_id}] Disconnected from voice channel: #{channel}")
        self.current_song = None
        await self.bot.change_presence(activity=None)

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    async def _prefetch_next(self, source: AudioSource) -> None:
        """
        Resolve and pre-create the FFmpegOpusAudio for the next queued song
        while the current song is still playing. This way the transition between
        songs requires no blocking work on the event loop.
        """
        next_song = self.queue.peek_next()
        if not next_song:
            return

        try:
            log.debug(f"[guild={self.guild_id}] Prefetching: {next_song.title!r}")
            resolved = await source.resolve(next_song)

            loop = asyncio.get_event_loop()
            audio = await loop.run_in_executor(
                None,
                lambda: discord.FFmpegOpusAudio(resolved.url, **FFMPEG_OPTIONS)
            )

            # Only store if the queue hasn't changed since we started prefetching
            if self.queue.peek_next() and self.queue.peek_next().title == next_song.title:
                self._prefetched_song = resolved
                self._prefetched_audio = audio
                log.debug(f"[guild={self.guild_id}] Prefetch ready: {resolved.title!r}")
            else:
                log.debug(f"[guild={self.guild_id}] Prefetch discarded (queue changed)")

        except Exception as e:
            # Prefetch failure is non-fatal — play_song will resolve normally as fallback
            log.warning(f"[guild={self.guild_id}] Prefetch failed for {next_song.title!r}: {e}")
            self._prefetched_song = None
            self._prefetched_audio = None

    def _clear_prefetch(self) -> None:
        """Discard any prefetched data, e.g. when the queue changes or we seek."""
        if self._prefetch_task and not self._prefetch_task.done():
            self._prefetch_task.cancel()
        self._prefetch_task = None
        self._prefetched_song = None
        self._prefetched_audio = None

    def _schedule_prefetch(self, source: AudioSource) -> None:
        """Schedule prefetch as a background task so it doesn't block play_song."""
        self._clear_prefetch()
        self._prefetch_task = asyncio.ensure_future(self._prefetch_next(source))

    async def play_song(self, song: Song, source: AudioSource, after_ctx) -> None:
        """
        Resolve the song's stream URL and begin playback.
        Uses prefetched audio if available, otherwise resolves on demand.
        On failure, attempts to skip to the next queued song.
        """
        self._last_activity = time()

        try:
            # Use prefetched data if it matches this song
            if (
                self._prefetched_song is not None
                and self._prefetched_audio is not None
                and self._prefetched_song.link == song.link
            ):
                log.debug(f"[guild={self.guild_id}] Using prefetched audio for: {song.title!r}")
                resolved = self._prefetched_song
                audio = self._prefetched_audio
                self._prefetched_song = None
                self._prefetched_audio = None
            else:
                log.debug(f"[guild={self.guild_id}] Resolving stream URL for: {song.title!r}")
                resolved = await source.resolve(song)

                loop = asyncio.get_event_loop()
                audio = await loop.run_in_executor(
                    None,
                    lambda: discord.FFmpegOpusAudio(resolved.url, **FFMPEG_OPTIONS)
                )

            self.current_song = resolved
            self.start_time = time()
            self._seek_position = 0.0

            source_name = type(source).__name__.replace("Source", "")
            artist_info = f" — {resolved.artist}" if resolved.artist else ""
            log.info(
                f"[guild={self.guild_id}] Now playing [{source_name}]: "
                f"{resolved.title!r}{artist_info} "
                f"(duration={resolved.duration}s, queue_remaining={len(self.queue)})"
            )

            self._voice_client.play(
                audio,
                after=lambda e: self._on_audio_error(e, after_ctx, source)
            )

            # Update bot presence to show the current song
            await self.bot.change_presence(
                activity=discord.Activity(
                    type=discord.ActivityType.playing,
                    name=f"🎵 Playing: {resolved.title}"
                )
            )

            # Start prefetching the next song in the background
            if not self.queue.is_empty():
                self._schedule_prefetch(source)

        except Exception as e:
            log.error(
                f"[guild={self.guild_id}] Failed to play {song.title!r}: {e}",
                exc_info=True
            )
            self.current_song = None
            await self._recover_from_error(
                after_ctx, source,
                f"Couldn't load **{song.title}** — skipping to next song."
            )

    def _on_audio_error(self, error, ctx, source: AudioSource) -> None:
        """
        Called by discord.py's AudioPlayer thread when FFmpeg dies mid-stream.
        Schedules _after_play normally if no error, or recovery if there was one.
        """
        if error:
            log.error(
                f"[guild={self.guild_id}] FFmpeg error mid-stream: {error}",
                exc_info=error
            )
            asyncio.run_coroutine_threadsafe(
                self._recover_from_error(
                    ctx, source,
                    "The stream dropped unexpectedly — skipping to next song."
                ),
                self.bot.loop
            )
        else:
            asyncio.run_coroutine_threadsafe(
                self._after_play(ctx, source), self.bot.loop
            )

    async def _recover_from_error(self, ctx, source: AudioSource, message: str) -> None:
        """
        Attempt to recover from a playback error by notifying the channel
        and advancing to the next queued song. Cleans up state regardless.
        """
        self.current_song = None
        self._clear_prefetch()

        if self._last_channel:
            try:
                await self._last_channel.send(message)
            except Exception:
                pass  # Don't let a send failure mask the original error

        next_song = self.queue.pop_next()
        if next_song:
            log.info(f"[guild={self.guild_id}] Recovering — advancing to: {next_song.title!r}")
            self.current_song = next_song
            await self.play_song(next_song, source, ctx)
        else:
            log.info(f"[guild={self.guild_id}] Recovery: queue exhausted, stopping.")
            if self._last_channel:
                try:
                    await self._last_channel.send("Nothing left in the queue to play.")
                except Exception:
                    pass

    async def _after_play(self, ctx, source: AudioSource) -> None:
        """Called automatically when a song finishes cleanly."""
        try:
            if self._stopping:
                self._stopping = False
                self.current_song = None
                log.debug(f"[guild={self.guild_id}] Playback stopped (stop/disconnect requested)")
                return

            if self._seeking:
                self._seeking = False
                log.debug(f"[guild={self.guild_id}] Seek cycle complete")
                return

            finished = self.current_song
            if finished:
                log.info(f"[guild={self.guild_id}] Finished: {finished.title!r}")

            next_song = self.queue.pop_next()
            if next_song:
                self.current_song = next_song
                log.info(f"[guild={self.guild_id}] Advancing queue → {next_song.title!r} ({len(self.queue)} remaining)")
                await self.play_song(next_song, source, ctx)
                if self._last_channel:
                    from ..ui.now_playing import send_now_playing
                    await send_now_playing(self._last_channel, self, self.bot)
            else:
                log.info(f"[guild={self.guild_id}] Queue exhausted, playback complete")
                self.current_song = None
                await self.bot.change_presence(activity=None)
        except Exception as e:
            log.error(
                f"[guild={self.guild_id}] Unexpected error in _after_play: {e}",
                exc_info=True
            )
            self.current_song = None

    def pause(self) -> bool:
        """Pause playback. Returns True if successful."""
        if self.is_playing:
            self._voice_client.pause()
            log.info(f"[guild={self.guild_id}] Paused: {self.current_song.title!r}")
            return True
        return False

    def resume(self) -> bool:
        """Resume playback. Returns True if successful."""
        if self.is_paused:
            self._voice_client.resume()
            log.info(f"[guild={self.guild_id}] Resumed: {self.current_song.title!r}")
            return True
        return False

    def stop(self) -> None:
        """Stop playback without advancing the queue (used for hard stop and disconnect)."""
        self._stopping = True
        self._clear_prefetch()
        if self._voice_client:
            self._voice_client.stop()
        if self.current_song:
            log.info(f"[guild={self.guild_id}] Stopped: {self.current_song.title!r}")
        self.current_song = None
        asyncio.ensure_future(self.bot.change_presence(activity=None))

    async def skip(self) -> bool:
        """
        Skip the current song. The after-callback handles playing the next one.
        Returns True if there was something to skip.
        """
        if not self.is_playing:
            return False
        log.info(f"[guild={self.guild_id}] Skipped: {self.current_song.title!r}")
        self._voice_client.stop()
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

        if was_paused:
            self._voice_client.resume()

        current_position = self._seek_position + (time() - self.start_time)
        new_position = max(0.0, current_position + seconds)

        if new_position >= self.current_song.duration:
            log.debug(
                f"[guild={self.guild_id}] Seek rejected: "
                f"target {new_position:.1f}s >= duration {self.current_song.duration}s"
            )
            return None

        log.info(
            f"[guild={self.guild_id}] Seek: {current_position:.1f}s → {new_position:.1f}s "
            f"({'+' if seconds >= 0 else ''}{seconds}s) on {self.current_song.title!r}"
        )

        self._seeking = True
        self._clear_prefetch()
        self._voice_client.stop()

        options = _ffmpeg_options_with_seek(new_position)
        loop = asyncio.get_event_loop()
        audio = await loop.run_in_executor(
            None,
            lambda: discord.FFmpegOpusAudio(self.current_song.url, **options)
        )
        self._voice_client.play(
            audio,
            after=lambda e: self._on_audio_error(e, ctx, source)
        )
        self.start_time = time()
        self._seek_position = new_position

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