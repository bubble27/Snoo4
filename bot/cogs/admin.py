from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.config import (
    ADMIN_USER_ID,
    AUTO_SAVE_INTERVAL,
    BOT_COLOR,
    EMOJIS,
    ICONS,
)
from bot.utils import format_time

if TYPE_CHECKING:
    from bot.data import DataManager

log = logging.getLogger(__name__)

ADMIN_DENIED = "You are not allowed to use this command!"


def _is_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.id == ADMIN_USER_ID


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot, data: DataManager) -> None:
        self.bot = bot
        self.data = data

    async def cog_load(self) -> None:
        self.auto_save_task.start()

    async def cog_unload(self) -> None:
        self.auto_save_task.cancel()

    # --- Periodic tasks ---

    @tasks.loop(seconds=AUTO_SAVE_INTERVAL)
    async def auto_save_task(self) -> None:
        profile_cog = self.bot.cogs.get("ProfileCog")
        if profile_cog:
            profile_cog.flush_all()
        await self.data.save_all(self.bot)
        # Evict stale YouTube cache entries to prevent unbounded memory growth
        yt = getattr(self.bot, "youtube", None)
        if yt:
            yt.evict_stale()

    @auto_save_task.before_loop
    async def before_auto_save(self) -> None:
        await self.bot.wait_until_ready()

    # --- Admin commands ---

    @app_commands.command(name="save", description="Manually save all data")
    async def save(self, interaction: discord.Interaction) -> None:
        if not _is_admin(interaction):
            await interaction.response.send_message(ADMIN_DENIED, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self.data.save_all(self.bot)
        await interaction.followup.send("\u2705 Saved", ephemeral=True)

    @app_commands.command(name="quit", description="Shut down the bot")
    async def quit_cmd(self, interaction: discord.Interaction) -> None:
        if not _is_admin(interaction):
            await interaction.response.send_message(ADMIN_DENIED, ephemeral=True)
            return
        await interaction.response.send_message("Shutting down...")
        await self.bot.close()

    @app_commands.command(name="restart", description="Restart the bot")
    async def restart_cmd(self, interaction: discord.Interaction) -> None:
        if not _is_admin(interaction):
            await interaction.response.send_message(ADMIN_DENIED, ephemeral=True)
            return
        await interaction.response.send_message("Restarting...")
        from main import request_restart
        request_restart()
        await self.bot.close()

    @app_commands.command(name="say", description="Send a message as the bot")
    @app_commands.describe(message="The message to send")
    async def say(self, interaction: discord.Interaction, message: str) -> None:
        if not _is_admin(interaction):
            await interaction.response.send_message(ADMIN_DENIED, ephemeral=True)
            return
        await interaction.response.send_message("Sent!", ephemeral=True)
        await interaction.channel.send(message)

    @app_commands.command(name="user", description="Get info about a user")
    @app_commands.describe(user="The user to look up")
    async def user_cmd(self, interaction: discord.Interaction, user: discord.User) -> None:
        if not _is_admin(interaction):
            await interaction.response.send_message(ADMIN_DENIED, ephemeral=True)
            return
        fetched = await self.bot.fetch_user(user.id)
        await interaction.response.send_message(str(fetched), ephemeral=True)

    @app_commands.command(name="history", description="Show your top 10 most listened songs")
    async def history(self, interaction: discord.Interaction) -> None:
        if not _is_admin(interaction):
            await interaction.response.send_message(ADMIN_DENIED, ephemeral=True)
            return

        top = self.data.get_top_songs(interaction.guild.id, interaction.user.id)
        result = [f"`{vid_id}` — {score}" for vid_id, score in top]
        await interaction.response.send_message("\n".join(result) or "No history.", ephemeral=True)

    # --- Poll command ---

    @app_commands.command(name="poll", description="Create a poll")
    @app_commands.describe(name="Poll title", options="Comma-separated options (max 7)")
    async def poll(self, interaction: discord.Interaction, name: str, options: str) -> None:
        lang = self.data.get_lang(interaction.guild.id)
        opts = [o.strip() for o in options.split(",")][:7]

        embed = discord.Embed(title=name, colour=BOT_COLOR)
        embed.set_author(name=lang["ui"]["title"]["poll"].title(), icon_url=ICONS["poll"])

        for i, opt in enumerate(opts):
            embed.add_field(
                name=f"{lang['ui']['field']['option'].title()}  {EMOJIS['poll'][i]}",
                value=opt,
                inline=False,
            )

        await interaction.response.send_message(embed=embed)
        msg = await interaction.original_response()
        for i in range(len(opts)):
            await msg.add_reaction(EMOJIS["poll"][i])

    # --- Playlist display ---

    @app_commands.command(name="playlist", description="Display a saved playlist")
    @app_commands.describe(name="Playlist name")
    async def playlist(self, interaction: discord.Interaction, name: str) -> None:
        lang = self.data.get_lang(interaction.guild.id)
        playlists = self.data.playlists.get(interaction.guild.id, {})
        if name not in playlists:
            await interaction.response.send_message("Playlist not found.", ephemeral=True)
            return

        pl = playlists[name]
        embed = discord.Embed(title=pl.get("title", name), description=pl.get("desc", ""), color=BOT_COLOR)
        embed.set_author(name=lang["ui"]["title"]["playlist"].title(), icon_url=ICONS["music"])
        embed.set_thumbnail(url=pl.get("cover", ""))

        yt = self.bot.youtube  # type: ignore[attr-defined]
        for song_id in pl.get("songs", []):
            vid = yt.cache.get(song_id, {})
            embed.add_field(name=vid.get("title", "Unknown"), value=vid.get("channel_name", ""), inline=True)
            embed.add_field(name="\u200b", value="\u200b", inline=True)
            embed.add_field(name=format_time(vid.get("secs_length", 0)), value="\u200b", inline=True)

        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot, bot.data))  # type: ignore[attr-defined]
