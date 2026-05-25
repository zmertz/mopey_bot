"""
bot.py — Bot setup, cog wiring, and entry point.

This is the only place that reads environment variables and
constructs the concrete source/cog objects. Everything else
receives its dependencies via constructor injection.
"""

import logging
import os
from dotenv import load_dotenv

import discord
from discord.ext import commands

from .core.sources import YouTubeSource, PlexSource
from .cogs.music import MusicCog
from .cogs.plex_cog import PlexCog
from .utils.log import setup_logging, get_logger

log = get_logger(__name__)


def run_bot():
    setup_logging(level=logging.INFO)
    load_dotenv()

    TOKEN = os.getenv("discord_token")
    PLEX_BASE_URL = os.getenv("plex_base_url")
    PLEX_TOKEN = os.getenv("plex_token")

    if not TOKEN:
        raise ValueError("discord_token not set in environment.")

    intents = discord.Intents.default()
    intents.message_content = True

    bot = commands.Bot(command_prefix=".", intents=intents)

    youtube = YouTubeSource()
    plex = PlexSource(PLEX_BASE_URL, PLEX_TOKEN) if PLEX_BASE_URL and PLEX_TOKEN else None

    async def setup():
        await bot.add_cog(MusicCog(bot, youtube))
        if plex:
            await bot.add_cog(PlexCog(bot, plex))
        else:
            log.warning("Plex not configured — .plex and .plexsearch commands unavailable.")

    @bot.event
    async def on_ready():
        log.info(f"Logged in as {bot.user} (id={bot.user.id})")
        log.info(f"Connected to {len(bot.guilds)} guild(s): {', '.join(g.name for g in bot.guilds)}")

    import asyncio

    async def main():
        async with bot:
            await setup()
            await bot.start(TOKEN)

    asyncio.run(main())