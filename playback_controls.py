from discord.ui import View, button
from discord import ButtonStyle, Interaction

class PlaybackControls(View):
    def __init__(self, ctx, client):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.client = client

    @button(label="►", style=ButtonStyle.green)
    async def play_button(self, interaction: Interaction, button):
        #await interaction.response.send_message("Resumed playback.", ephemeral=True)
        await interaction.response.defer()  # silently acknowledge
        await self.ctx.invoke(self.client.get_command("resume"))

    @button(label="⏸", style=ButtonStyle.blurple)
    async def pause_button(self, interaction: Interaction, button):
        #await interaction.response.send_message("Paused playback.", ephemeral=True)
        await interaction.response.defer()  # silently acknowledge
        await self.ctx.invoke(self.client.get_command("pause"))

    @button(label="⟲", style=ButtonStyle.blurple)
    async def restart_button(self, interaction: Interaction, button):
        #await interaction.response.send_message("Restarted playback.", ephemeral=True)
        await interaction.response.defer()  # silently acknowledge
        await self.ctx.invoke(self.client.get_command("seek"), seconds=-100000)

    @button(label="⏭", style=ButtonStyle.blurple)
    async def skip_button(self, interaction: Interaction, button):
        #await interaction.response.send_message("Skipped to the next song.", ephemeral=True)
        await interaction.response.defer()  # silently acknowledge
        await self.ctx.invoke(self.client.get_command("skip"))

    @button(label="⏹", style=ButtonStyle.danger)
    async def stop_button(self, interaction: Interaction, button):
        #await interaction.response.send_message("Stopped playback.", ephemeral=True)
        await interaction.response.defer()  # silently acknowledge
        await self.ctx.invoke(self.client.get_command("stop"))
