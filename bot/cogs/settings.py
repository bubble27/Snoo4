from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, Select, View

from bot.config import BOT_COLOR, EMOJIS, ICONS, SETTINGS_META

if TYPE_CHECKING:
    from bot.data import DataManager

log = logging.getLogger(__name__)


class SettingsCog(commands.Cog):
    def __init__(self, bot: commands.Bot, data: DataManager) -> None:
        self.bot = bot
        self.data = data

    @app_commands.command(name="settings", description="View and change server settings")
    async def settings(self, interaction: discord.Interaction) -> None:
        self.data.verify_settings(interaction.guild.id)
        embed, view = self._build_settings(interaction.guild.id)
        await interaction.response.send_message(embed=embed, view=view)

    def _build_settings(self, guild_id: int) -> tuple[discord.Embed, View]:
        config = self.data.server_config[guild_id]
        lang = self.data.get_lang(guild_id)

        embed = discord.Embed(colour=BOT_COLOR)
        embed.set_author(name=lang["ui"]["title"]["settings"].title(), icon_url=ICONS["settings"])

        # Language row
        embed.add_field(
            name=lang["setting_names"]["language"].title(),
            value=lang["settings_info"]["language"],
            inline=True,
        )
        embed.add_field(name="\u200b", value="\u200b", inline=True)
        embed.add_field(
            name=f'{lang["flag"]} {config["lang_set"]}',
            value="\u200b",
            inline=True,
        )

        view = View(timeout=None)

        # Language selector
        options = [
            discord.SelectOption(
                label=name,
                emoji=lang_data["flag"],
                default=(name == config["lang_set"]),
            )
            for name, lang_data in self.data.language.items()
        ]
        select = Select(placeholder=lang["ui"]["field"]["select_language"], options=options)

        async def on_lang_change(interaction: discord.Interaction):
            config["lang_set"] = select.values[0]
            new_embed, new_view = self._build_settings(guild_id)
            await interaction.message.edit(embed=new_embed, view=new_view)
            await interaction.response.defer()

        select.callback = on_lang_change
        view.add_item(select)

        # Toggle buttons for each non-dev setting
        for key, value in config.items():
            meta = SETTINGS_META.get(key)
            if meta is None or meta.get("dev"):
                continue

            embed.add_field(name=lang["setting_names"][key].title(), value=lang["settings_info"][key], inline=True)
            embed.add_field(name="\u200b", value="\u200b", inline=True)
            embed.add_field(name=EMOJIS["on"] if value else EMOJIS["off"], value="\u200b", inline=True)

            btn = Button(label=lang["setting_names"][key].title())
            btn.custom_id = key

            async def on_toggle(interaction: discord.Interaction):
                setting = interaction.data["custom_id"].lower()
                if setting in config:
                    config[setting] = not config[setting]
                    new_embed, new_view = self._build_settings(guild_id)
                    await interaction.message.edit(embed=new_embed, view=new_view)
                    await interaction.response.defer()
                else:
                    await interaction.response.send_message(lang["error"]["setting_not_found"], ephemeral=True)

            btn.callback = on_toggle
            view.add_item(btn)

        return embed, view


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SettingsCog(bot, bot.data))  # type: ignore[attr-defined]
