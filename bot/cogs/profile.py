from __future__ import annotations

import logging
from math import fsum
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands
from pandas import DataFrame
from plotly.express import line as px_line

from bot.config import BOT_COLOR, EMOJIS, ICONS
from bot.utils import find_urls

if TYPE_CHECKING:
    from bot.data import DataManager

log = logging.getLogger(__name__)


class ProfileCog(commands.Cog):
    def __init__(self, bot: commands.Bot, data: DataManager) -> None:
        self.bot = bot
        self.data = data

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return

        guild_id = message.guild.id
        channel_id = message.channel.id

        # Track channel message count
        channels = self.data.channel_messages
        if guild_id not in channels:
            channels[guild_id] = {}
        if channel_id not in channels[guild_id]:
            channels[guild_id][channel_id] = [1]
        else:
            channels[guild_id][channel_id][-1] += 1

        # Track user message count
        profile = self.data.verify_profile(guild_id, message.author.id)
        profile["messages"][-1] += 1

        # Auto-vote on image posts
        settings = self.data.verify_settings(guild_id)
        if settings.get("votes"):
            has_media = (
                message.attachments
                or find_urls(message.content)
                or "*image*" in message.content.lower()
            )
            if has_media:
                await self._add_votes(message, settings)

        # Scale / poll reactions
        content = message.content.lower()
        if content.startswith("%"):
            if "scale" in content:
                for num in EMOJIS["numbers"]:
                    await message.add_reaction(num)
            else:
                await message.add_reaction(EMOJIS["cross"])
                await message.add_reaction(EMOJIS["check"])

    async def _add_votes(self, message: discord.Message, settings: dict) -> None:
        upvote = None
        downvote = None

        for emoji in message.guild.emojis:
            name = emoji.name.lower()
            if name == "upvote":
                upvote = emoji
            elif name == "downvote":
                downvote = emoji
            if upvote and (downvote or not settings.get("downvote")):
                break

        await message.add_reaction(upvote or EMOJIS["upvote"])
        if settings.get("downvote"):
            await message.add_reaction(downvote or EMOJIS["downvote"])

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User) -> None:
        await self._handle_reaction(reaction, user, added=True)

    @commands.Cog.listener()
    async def on_reaction_remove(self, reaction: discord.Reaction, user: discord.User) -> None:
        await self._handle_reaction(reaction, user, added=False)

    async def _handle_reaction(self, reaction: discord.Reaction, user: discord.User, added: bool) -> None:
        if user == self.bot.user or user == reaction.message.author:
            return
        if isinstance(reaction.emoji, str):
            return

        guild_id = reaction.message.guild.id
        author_id = reaction.message.author.id
        name = reaction.emoji.name.lower()

        if name == "upvote":
            karma_delta = 1 if added else -1
        elif name == "downvote":
            karma_delta = -1 if added else 1
        else:
            return

        author_profile = self.data.verify_profile(guild_id, author_id)
        author_profile["karma"][-1] += karma_delta

        voter_profile = self.data.verify_profile(guild_id, user.id)
        voter_profile["friendship"][-1] += karma_delta

    @app_commands.command(name="profile", description="View a user's profile stats")
    @app_commands.describe(user="The user to view (defaults to yourself)")
    async def profile(self, interaction: discord.Interaction, user: discord.User = None) -> None:
        lang = self.data.get_lang(interaction.guild.id)
        target = user or interaction.user
        user_obj = await self.bot.fetch_user(target.id)
        username = user_obj.display_name

        profile = self.data.verify_profile(interaction.guild.id, target.id)

        embed = discord.Embed(colour=BOT_COLOR)
        embed.set_author(
            name=lang["ui"]["title"]["profile"].title().format(username),
            icon_url=ICONS["profile"],
        )

        fields = [
            ("karma", sum(profile["karma"])),
            ("friendship", sum(profile["friendship"])),
            ("messages", sum(profile["messages"])),
            ("vc_hours", round(fsum(profile["vc_time"]), 1)),
        ]
        for i, (key, value) in enumerate(fields):
            field_data = lang["ui"]["field"][key]
            embed.add_field(name=field_data["title"].title(), value=field_data["desc"].format(value), inline=True)
            if i % 2 == 0:
                embed.add_field(name="\u200b", value="\u200b", inline=True)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="graph", description="Graph a profile stat over time")
    @app_commands.describe(stat_type="The stat to graph (messages, karma, friendship, vc_time)", user="The user to graph")
    @app_commands.choices(stat_type=[
        app_commands.Choice(name="Messages", value="messages"),
        app_commands.Choice(name="Karma", value="karma"),
        app_commands.Choice(name="Friendship", value="friendship"),
        app_commands.Choice(name="VC Time", value="vc_time"),
    ])
    async def graph(self, interaction: discord.Interaction, stat_type: app_commands.Choice[str], user: discord.User) -> None:
        profile = self.data.profile_data.get(interaction.guild.id, {}).get(user.id)
        if profile is None or stat_type.value not in profile:
            await interaction.response.send_message("No data found.", ephemeral=True)
            return

        await interaction.response.defer()
        df = DataFrame(profile[stat_type.value], columns=[stat_type.name])
        fig = px_line(df, markers=False, template="seaborn")
        fig["data"][0]["line"]["color"] = "#FF4400"
        fig.write_image("Cache/graph.png")
        await interaction.followup.send(file=discord.File("Cache/graph.png"))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ProfileCog(bot, bot.data))  # type: ignore[attr-defined]
