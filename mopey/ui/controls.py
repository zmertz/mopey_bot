"""
PlaybackControls — the button row shown under Now Playing embeds.

Key improvement over the original: buttons call the MusicCog's player
methods directly instead of re-invoking commands by string name.
This means they work correctly even if command names change.
"""

import discord
from discord.ui import View, button
from discord import ButtonStyle, Interaction


class PlaybackControls(View):

    def __init__(self, ctx_or_channel, bot: discord.ext.commands.Bot):
        super().__init__(timeout=None)
        self._ctx_or_channel = ctx_or_channel
        self._bot = bot

    def _get_player(self, guild_id: int):
        """Retrieve the GuildPlayer from the MusicCog."""
        cog = self._bot.cogs.get("MusicCog")
        if cog is None:
            return None
        return cog.get_player(guild_id)

    def _guild_id(self, interaction: Interaction) -> int:
        return interaction.guild_id

    @button(label="►", style=ButtonStyle.green)
    async def play_button(self, interaction: Interaction, btn):
        await interaction.response.defer()
        player = self._get_player(self._guild_id(interaction))
        if player:
            player.resume()

    @button(label="⏸", style=ButtonStyle.blurple)
    async def pause_button(self, interaction: Interaction, btn):
        await interaction.response.defer()
        player = self._get_player(self._guild_id(interaction))
        if player:
            player.pause()

    @button(label="⟲", style=ButtonStyle.blurple)
    async def restart_button(self, interaction: Interaction, btn):
        await interaction.response.defer()
        player = self._get_player(self._guild_id(interaction))
        cog = self._bot.cogs.get("MusicCog")
        if player and cog:
            # Seek to beginning by seeking far back
            await player.seek(-99999, cog.get_source_for_player(player), self._ctx_or_channel)

    @button(label="⏭", style=ButtonStyle.blurple)
    async def skip_button(self, interaction: Interaction, btn):
        await interaction.response.defer()
        player = self._get_player(self._guild_id(interaction))
        if player:
            await player.skip()

    @button(label="⏹", style=ButtonStyle.danger)
    async def stop_button(self, interaction: Interaction, btn):
        await interaction.response.defer()
        player = self._get_player(self._guild_id(interaction))
        if player:
            await player.disconnect()
