"""
MusicCog — all Discord music commands.

Commands are thin: they validate input, get/create the GuildPlayer,
and delegate to it. No business logic lives here.
"""

import discord
from discord.ext import commands, tasks

from ..core.player import GuildPlayer
from ..core.sources import AudioSource, YouTubeSource
from ..core.song import Song
from ..ui.now_playing import send_now_playing
from ..ui.search_menu import show_search_results
from ..utils.formatting import format_time, format_song_line
from ..utils.log import get_logger

log = get_logger(__name__)


class MusicCog(commands.Cog, name="MusicCog"):

    def __init__(self, bot: commands.Bot, youtube: YouTubeSource):
        self.bot = bot
        self._youtube = youtube
        self._players: dict[int, GuildPlayer] = {}
        # Track which source a player is currently using so UI buttons can seek
        self._player_sources: dict[int, AudioSource] = {}

    # ------------------------------------------------------------------
    # Player management
    # ------------------------------------------------------------------

    def get_player(self, guild_id: int) -> GuildPlayer | None:
        return self._players.get(guild_id)

    def get_or_create_player(self, guild_id: int) -> GuildPlayer:
        if guild_id not in self._players:
            self._players[guild_id] = GuildPlayer(guild_id, self.bot)
        return self._players[guild_id]

    def get_source_for_player(self, player: GuildPlayer) -> AudioSource:
        return self._player_sources.get(player.guild_id, self._youtube)

    async def _ensure_connected(self, ctx) -> GuildPlayer | None:
        """
        Get or create a player for this guild, connecting to the user's voice
        channel if not already connected. Returns None and sends an error if
        the user isn't in a voice channel.
        """
        player = self.get_or_create_player(ctx.guild.id)
        player.update_activity(ctx.channel)

        if not player.is_connected:
            if ctx.author.voice and ctx.author.voice.channel:
                await player.connect(ctx.author.voice.channel)
            else:
                await ctx.send("You must be in a voice channel.")
                return None

        return player

    async def _play_or_queue(
        self, ctx, song: Song, source: AudioSource
    ) -> None:
        player = await self._ensure_connected(ctx)
        if not player:
            return

        user = f"{ctx.author.name}#{ctx.author.discriminator}"
        if player.is_playing:
            if player.queue.is_full():
                log.warning(f"[guild={ctx.guild.id}] Queue full, rejected: {song.title!r} (user={user})")
                await ctx.send("The queue is full. Please wait for some songs to finish.")
                return
            player.queue.add(song)
            position = len(player.queue)
            log.info(f"[guild={ctx.guild.id}] Queued at #{position}: {song.title!r} (user={user})")
            line = format_song_line(song.title, song.duration, song.artist, song.album)
            await ctx.send(f"Added to queue: **{line}** (Position: {position})")
        else:
            log.info(f"[guild={ctx.guild.id}] Playing immediately: {song.title!r} (user={user})")
            self._player_sources[ctx.guild.id] = source
            await player.play_song(song, source, ctx)
            await send_now_playing(ctx, player, self.bot)

    # ------------------------------------------------------------------
    # Inactivity loop
    # ------------------------------------------------------------------

    @tasks.loop(seconds=120)
    async def _inactivity_check(self):
        for player in list(self._players.values()):
            disconnected = await player.check_inactivity()
            if disconnected:
                del self._players[player.guild_id]

    @commands.Cog.listener()
    async def on_ready(self):
        if not self._inactivity_check.is_running():
            self._inactivity_check.start()

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @commands.command(name="join")
    async def join(self, ctx):
        """Join the user's current voice channel."""
        player = self.get_or_create_player(ctx.guild.id)

        if player.is_connected:
            await ctx.send("I'm already in a channel!")
            return

        if not ctx.author.voice:
            await ctx.send("You need to be in a voice channel to use this command.")
            return

        await player.connect(ctx.author.voice.channel)
        player.update_activity(ctx.channel)
        await ctx.send("Connected to the voice channel!")

    @commands.command(name="play")
    async def play(self, ctx, *, link: str = None):
        """
        Play a song by YouTube URL/search query, or resume if paused.
        Usage: .play <url or search terms>
        """
        if link is None:
            player = self.get_player(ctx.guild.id)
            if player and player.is_paused:
                player.resume()
                await ctx.send("Resumed the music!")
            else:
                await ctx.send("No music is currently paused to resume.")
            return

        log.info(f"[guild={ctx.guild.id}] .play invoked by {ctx.author.name}: {link!r}")
        await ctx.send("Loading...")
        try:
            songs = await self._youtube.search(link, limit=1)
            if not songs:
                log.warning(f"[guild={ctx.guild.id}] No results for: {link!r}")
                await ctx.send("Could not find that song.")
                return
            await self._play_or_queue(ctx, songs[0], self._youtube)
        except Exception as e:
            log.error(f"[guild={ctx.guild.id}] Error in .play ({link!r}): {e}", exc_info=True)
            await ctx.send("An error occurred while trying to play the song.")

    @commands.command(name="search")
    async def search(self, ctx, *, query: str = None):
        """
        Search YouTube and pick from the top 3 results.
        Usage: .search <query>
        """
        if not query:
            await ctx.send("Please provide a search query.")
            return

        log.info(f"[guild={ctx.guild.id}] .search invoked by {ctx.author.name}: {query!r}")
        await ctx.send("Searching...")
        try:
            songs = await self._youtube.search(query, limit=3)
            chosen = await show_search_results(ctx, songs, title="YouTube Search Results")
            if chosen:
                log.info(f"[guild={ctx.guild.id}] Search selection: {chosen.title!r} (user={ctx.author.name})")
                await self._play_or_queue(ctx, chosen, self._youtube)
        except Exception as e:
            log.error(f"[guild={ctx.guild.id}] Error in .search ({query!r}): {e}", exc_info=True)
            await ctx.send("An error occurred while processing your search.")

    @commands.command(name="playing")
    async def playing(self, ctx):
        """Show the currently playing song."""
        player = self.get_player(ctx.guild.id)
        if not player or not player.current_song:
            await ctx.send("No song is currently playing.")
            return
        await send_now_playing(ctx, player, self.bot)

    @commands.command(name="queue")
    async def queue(self, ctx):
        """Show the current queue."""
        player = self.get_player(ctx.guild.id)
        if not player or player.queue.is_empty():
            await ctx.send("The queue is empty!")
            return

        embed = discord.Embed(title="🎶 Current Queue 🎶", color=discord.Color.blurple())
        for idx, song in enumerate(player.queue[:5], start=1):
            line = format_song_line(song.title, song.duration, song.artist, song.album)
            embed.add_field(name="\u200b", value=f"**{idx}.** {line}", inline=False)

        remaining = len(player.queue) - 5
        if remaining > 0:
            embed.add_field(
                name="\u200b",
                value=f"*...and {remaining} more song{'s' if remaining != 1 else ''} in the queue.*",
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.command(name="clear")
    async def clear(self, ctx):
        """Clear the queue."""
        player = self.get_player(ctx.guild.id)
        if not player or player.queue.is_empty():
            await ctx.send("There is no queue to clear.")
            return
        player.queue.clear()
        await ctx.send("Queue cleared!")

    @commands.command(name="remove")
    async def remove(self, ctx, position: int = 0):
        """Remove a song from the queue by position. Usage: .remove <position>"""
        player = self.get_player(ctx.guild.id)
        if not player:
            await ctx.send("Nothing is playing.")
            return
        removed = player.queue.remove_at(position)
        if removed:
            line = format_song_line(removed.title, removed.duration)
            await ctx.send(f"Removed: **{line}** from the queue.")
        else:
            await ctx.send("Invalid position. Please provide a valid number within the queue.")

    @commands.command(name="playqueue")
    async def playqueue(self, ctx, position: int = 0):
        """Jump to a specific song in the queue. Usage: .playqueue <position>"""
        player = self.get_player(ctx.guild.id)
        if not player:
            await ctx.send("Nothing is playing.")
            return
        song = player.queue.move_to_front(position)
        if song:
            line = format_song_line(song.title, song.duration)
            await ctx.send(f"Grabbing **{line}** from the queue.")
            if player.is_playing or player.is_paused:
                player.stop()
                source = self.get_source_for_player(player)
                await player.play_song(song, source, ctx)
                player.queue.pop_next()  # remove it since we played it directly
                await send_now_playing(ctx, player, self.bot)
        else:
            await ctx.send("Invalid position. Please provide a valid number within the queue.")

    @commands.command(name="pause")
    async def pause(self, ctx):
        """Pause the currently playing song."""
        player = self.get_player(ctx.guild.id)
        if player and player.pause():
            await ctx.send("Music paused!")
        else:
            await ctx.send("Nothing is currently playing.")

    @commands.command(name="resume")
    async def resume(self, ctx):
        """Resume the paused song."""
        player = self.get_player(ctx.guild.id)
        if player and player.resume():
            await ctx.send("Music resumed!")
        else:
            await ctx.send("Nothing is currently paused.")

    @commands.command(name="stop")
    async def stop(self, ctx):
        """Stop playback and disconnect."""
        player = self.get_player(ctx.guild.id)
        if player:
            await player.disconnect()
            del self._players[ctx.guild.id]
            await ctx.send("Music stopped and disconnected.")
        else:
            await ctx.send("I'm not connected to a voice channel.")

    @commands.command(name="skip")
    async def skip(self, ctx):
        """Skip the current song."""
        player = self.get_player(ctx.guild.id)
        if not player or not player.is_connected:
            await ctx.send("I'm not connected to a voice channel.")
            return
        if not player.is_playing:
            await ctx.send("There's nothing to skip.")
            return

        if player.queue.is_empty():
            player.stop()
            await ctx.send("Song skipped. No more songs in the queue.")
        else:
            await player.skip()
            await ctx.send("Song skipped, now playing next in the queue.")

    @commands.command(name="seek")
    async def seek(self, ctx, seconds: int):
        """
        Seek forward or backward in the current song.
        Usage: .seek <seconds>  (negative to rewind)
        """
        player = self.get_player(ctx.guild.id)
        if not player:
            await ctx.send("No song is currently playing.")
            return

        source = self.get_source_for_player(player)
        new_pos = await player.seek(seconds, source, ctx)

        if new_pos is None:
            if not player.is_playing:
                await ctx.send("No song is currently playing.")
            else:
                await ctx.send("Cannot seek beyond the length of the song.")
        elif int(new_pos) == 0:
            await ctx.send("Restarting song.")
        else:
            await ctx.send(f"Seeked to {format_time(int(new_pos))}.")

    @commands.command(name="commands")
    async def commands_list(self, ctx):
        """Show all available commands."""
        embed = discord.Embed(title="Available Commands", color=discord.Color.blurple())
        command_list = [
            ("**.clear**",              "Clear the entire queue"),
            ("**.commands**",           "Show this list of commands"),
            ("**.join**",               "Join your current voice channel"),
            ("**.pause**",              "Pause the currently playing song"),
            ("**.play <link>**",        "Play a song (YouTube link, search query, or resume if paused)"),
            ("**.playqueue <pos>**",    "Jump to a specific song in the queue"),
            ("**.playing**",            "Show current song info"),
            ("**.plex <query>**",       "Play the first Plex result for a query"),
            ("**.plexsearch <query>**", "Search Plex and pick from results"),
            ("**.queue**",              "Show the current queue"),
            ("**.remove <pos>**",       "Remove a song from the queue by position"),
            ("**.resume**",             "Resume the paused song"),
            ("**.search <query>**",     "Search YouTube and pick from results"),
            ("**.seek <seconds>**",     "Seek forward/backward in the current song"),
            ("**.skip**",               "Skip the current song"),
            ("**.stop**",               "Stop and disconnect"),
        ]
        for name, description in command_list:
            embed.add_field(name=name, value=description, inline=False)
        await ctx.send(embed=embed)