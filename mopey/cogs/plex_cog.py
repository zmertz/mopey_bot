"""
PlexCog — Plex-specific commands.

Completely separate from MusicCog. Delegates play/queue logic back
to MusicCog so we don't duplicate it.
"""

from discord.ext import commands

from ..core.sources import PlexSource
from ..ui.search_menu import show_search_results
from ..utils.log import get_logger
import discord

log = get_logger(__name__)


class PlexCog(commands.Cog, name="PlexCog"):

    def __init__(self, bot: commands.Bot, plex: PlexSource):
        self.bot = bot
        self._plex = plex

    def _music_cog(self):
        """Get the MusicCog to delegate play/queue logic."""
        return self.bot.cogs.get("MusicCog")

    @commands.command(name="plex")
    async def plex(self, ctx, *, query: str = None):
        """
        Play the first Plex result for a query.
        Usage: .plex <query>
        """
        if not query:
            await ctx.send("Please provide a song name to search on Plex.")
            return

        log.info(f"[guild={ctx.guild.id}] .plex invoked by {ctx.author.name}: {query!r}")
        await ctx.send(f"Searching Plex for '{query}'...")
        try:
            songs = await self._plex.search(query, limit=1)
            if not songs:
                await ctx.send("No songs found on Plex.")
                return

            music = self._music_cog()
            if not music:
                await ctx.send("Music system is unavailable.")
                return

            await music._play_or_queue(ctx, songs[0], self._plex)

        except Exception as e:
            log.error(f"[guild={ctx.guild.id}] Error in .plex ({query!r}): {e}", exc_info=True)
            await ctx.send("An error occurred while trying to play from Plex.")

    @commands.command(name="plexsearch")
    async def plexsearch(self, ctx, *, query: str = None):
        """
        Search Plex and pick from the top 3 results.
        Usage: .plexsearch <query>
        """
        if not query:
            await ctx.send("Please provide a search query for Plex.")
            return

        log.info(f"[guild={ctx.guild.id}] .plexsearch invoked by {ctx.author.name}: {query!r}")
        await ctx.send("Searching Plex...")
        try:
            songs = await self._plex.search(query, limit=3)
            chosen = await show_search_results(
                ctx,
                songs,
                title="Plex Search Results",
                color=discord.Color.dark_gold(),
            )
            if chosen:
                log.info(f"[guild={ctx.guild.id}] Plex search selection: {chosen.title!r} (user={ctx.author.name})")
                music = self._music_cog()
                if not music:
                    await ctx.send("Music system is unavailable.")
                    return
                await music._play_or_queue(ctx, chosen, self._plex)

        except Exception as e:
            log.error(f"[guild={ctx.guild.id}] Error in .plexsearch ({query!r}): {e}", exc_info=True)
            await ctx.send("An error occurred while processing your Plex search.")