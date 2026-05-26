"""
Now Playing embed UI.

Separated from command logic so it can be called from both
the .playing command and the auto-advance after-callback.
"""

import discord

from ..utils.formatting import format_time, format_song_line, build_progress_bar


async def send_now_playing(
    destination: discord.abc.Messageable,
    player,   # GuildPlayer — avoiding circular import with string annotation
    bot: discord.ext.commands.Bot,
) -> None:
    """
    Build and send a Now Playing embed to `destination`.
    Works with any Messageable (TextChannel or Context).
    """
    from ..ui.controls import PlaybackControls  # local import avoids circular dep

    song = player.current_song
    if not song:
        await destination.send("No song is currently playing.")
        return

    elapsed = player.elapsed
    duration = song.duration

    detail_line = format_song_line(song.title, duration, song.artist, song.album)

    next_song = player.queue.peek_next()
    if next_song:
        next_detail = format_song_line(
            next_song.title, next_song.duration, next_song.artist, next_song.album
        )
    else:
        next_detail = "`Nothing next in queue`"

    embed = discord.Embed(
        title="Now Playing",
        description=detail_line,
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Progress",
        value=(
            f"▶️ {format_time(elapsed)} "
            f"{build_progress_bar(elapsed, duration)} "
            f"{format_time(duration)}"
        ),
        inline=False,
    )
    embed.add_field(name="Next", value=next_detail, inline=False)

    if song.thumbnail:
        embed.set_thumbnail(url=song.thumbnail)

    # Pass the destination as ctx-like object for the controls
    view = PlaybackControls(destination, bot)
    await destination.send(embed=embed, view=view)