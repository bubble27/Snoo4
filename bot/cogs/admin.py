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
        self.daily_entry_check.start()

    async def cog_unload(self) -> None:
        self.auto_save_task.cancel()
        self.daily_entry_check.cancel()

    # --- Periodic tasks ---

    @tasks.loop(seconds=AUTO_SAVE_INTERVAL)
    async def auto_save_task(self) -> None:
        users_in_vc = self._get_users_in_vc()
        self.data.update_vc_time(users_in_vc)
        await self.data.save_all(self.bot)

    @auto_save_task.before_loop
    async def before_auto_save(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=1)
    async def daily_entry_check(self) -> None:
        from datetime import datetime
        if datetime.now().strftime("%H:%M") == "05:00":
            self.data.add_daily_entry()
            log.info("Daily entry added")

    @daily_entry_check.before_loop
    async def before_daily_check(self) -> None:
        await self.bot.wait_until_ready()

    # --- Helper ---

    def _get_users_in_vc(self) -> dict[int, list[int]]:
        result: dict[int, list[int]] = {}
        for guild in self.bot.guilds:
            for vc in guild.voice_channels:
                for member in vc.members:
                    if member.bot:
                        continue
                    result.setdefault(guild.id, []).append(member.id)
        return result

    # --- Admin commands ---

    @app_commands.command(name="save", description="Manually save all data")
    async def save(self, interaction: discord.Interaction) -> None:
        if not _is_admin(interaction):
            await interaction.response.send_message(ADMIN_DENIED, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self.data.save_all(self.bot)
        await interaction.followup.send("\u2705 Saved", ephemeral=True)

    @app_commands.command(name="add_entry", description="Manually add a daily entry")
    async def add_entry(self, interaction: discord.Interaction) -> None:
        if not _is_admin(interaction):
            await interaction.response.send_message(ADMIN_DENIED, ephemeral=True)
            return
        self.data.add_daily_entry()
        await interaction.response.send_message("\u2705 Entry added", ephemeral=True)

    @app_commands.command(name="quit", description="Shut down the bot")
    async def quit_cmd(self, interaction: discord.Interaction) -> None:
        if not _is_admin(interaction):
            await interaction.response.send_message(ADMIN_DENIED, ephemeral=True)
            return
        await interaction.response.send_message("Shutting down...")
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

        guild_id = interaction.guild.id
        user_id = interaction.user.id
        user_history = self.data.song_history.get(guild_id, {}).get(user_id, [])

        totals: dict[str, float] = {}
        for day in user_history:
            if not isinstance(day, dict):
                continue
            for video_id, watches in day.items():
                for watch in watches:
                    totals[video_id] = round(totals.get(video_id, 0) + watch.get("retention", 0), 2)

        sorted_vids = sorted(totals.items(), key=lambda x: x[1], reverse=True)[:10]
        result = [vid_id for vid_id, _ in sorted_vids]
        await interaction.response.send_message(str(result), ephemeral=True)

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
