from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import ActionRow, Button, Container, LayoutView, Section, Select, Separator, TextDisplay

from bot.config import BOT_COLOR, EMOJIS, SETTINGS_META

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
        view = self._build_settings(interaction.guild.id)
        await interaction.response.send_message(view=view)

    def _build_settings(self, guild_id: int) -> LayoutView:
        config = self.data.server_config[guild_id]
        lang = self.data.get_lang(guild_id)

        view = LayoutView(timeout=None)

        # Build all sections inside a container
        children = []

        # Title + divider
        children.append(TextDisplay(f"## {lang['ui']['title']['settings'].title()}"))
        children.append(Separator())

        # Language label + dropdown
        children.append(TextDisplay(
            f"**{lang['setting_names']['language'].title()}**\n"
            f"{lang['settings_info']['language']}"
        ))

        options = [
            discord.SelectOption(
                label=name,
                emoji=lang_data["flag"],
                default=(name == config["lang_set"]),
            )
            for name, lang_data in sorted(self.data.language.items(), key=lambda x: x[0].lower())
        ]
        select = Select(placeholder=f'{lang["flag"]} {config["lang_set"]}', options=options)

        async def on_lang_change(interaction: discord.Interaction):
            config["lang_set"] = select.values[0]
            new_view = self._build_settings(guild_id)
            await interaction.response.edit_message(view=new_view)

        select.callback = on_lang_change
        children.append(ActionRow(select))

        # Toggle settings — Section with on/off button
        for key, value in config.items():
            meta = SETTINGS_META.get(key)
            if meta is None or meta.get("dev"):
                continue

            style = discord.ButtonStyle.success if value else discord.ButtonStyle.secondary
            emoji = "\u2714\ufe0f" if value else "\u2716\ufe0f"
            btn = Button(emoji=emoji, style=style)
            btn.custom_id = key

            async def on_toggle(interaction: discord.Interaction):
                setting = interaction.data["custom_id"].lower()
                if setting in config:
                    config[setting] = not config[setting]
                    new_view = self._build_settings(guild_id)
                    await interaction.response.edit_message(view=new_view)
                else:
                    await interaction.response.send_message(
                        lang["error"]["setting_not_found"], ephemeral=True,
                    )

            btn.callback = on_toggle

            children.append(Section(
                TextDisplay(
                    f"**{lang['setting_names'][key].title()}**\n"
                    f"{lang['settings_info'][key]}"
                ),
                accessory=btn,
            ))

        container = Container(*children, accent_colour=BOT_COLOR)
        view.add_item(container)
        return view


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SettingsCog(bot, bot.data))  # type: ignore[attr-defined]
