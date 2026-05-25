"""
Reusable reaction-based search result picker.

Both the YouTube search and Plex search commands show a list of results
and wait for the user to react with 1️⃣/2️⃣/3️⃣ or ❌.
Extracted here so the two cogs don't duplicate this logic.
"""

import asyncio
from typing import Optional

import discord

from ..core.song import Song
from ..utils.formatting import format_time, format_song_line

RESULT_EMOJIS = ["1️⃣", "2️⃣", "3️⃣"]
CANCEL_EMOJI = "❌"
REACTION_TIMEOUT = 30.0


async def show_search_results(
    ctx,
    songs: list[Song],
    title: str = "Search Results",
    color: discord.Color = discord.Color.dark_gray(),
) -> Optional[Song]:
    """
    Display `songs` (up to 3) in an embed, add number reactions, and wait
    for the user to pick one. Returns the chosen Song, or None if canceled/timed out.
    """
    if not songs:
        await ctx.send("No results found.")
        return None

    songs = songs[:3]
    reactions = RESULT_EMOJIS[: len(songs)] + [CANCEL_EMOJI]

    embed = discord.Embed(title=title, color=color)
    for i, song in enumerate(songs, start=1):
        line = format_song_line(song.title, song.duration, song.artist, song.album)
        embed.add_field(
            name=f"{RESULT_EMOJIS[i - 1]}",
            value=f"`[{format_time(song.duration)}]` {line}",
            inline=False,
        )

    message = await ctx.send(embed=embed)
    for emoji in reactions:
        await message.add_reaction(emoji)

    def check(reaction, user):
        return (
            user == ctx.author
            and str(reaction.emoji) in reactions
            and reaction.message.id == message.id
        )

    try:
        reaction, _ = await ctx.bot.wait_for("reaction_add", timeout=REACTION_TIMEOUT, check=check)
    except asyncio.TimeoutError:
        await ctx.send("You took too long to respond! Search canceled.")
        return None

    if str(reaction.emoji) == CANCEL_EMOJI:
        await ctx.send("Search canceled.")
        return None

    index = RESULT_EMOJIS.index(str(reaction.emoji))
    return songs[index]
