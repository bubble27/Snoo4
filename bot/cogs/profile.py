from __future__ import annotations

import logging
from datetime import datetime
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
        # Tracks when each user joined VC: {(guild_id, user_id): datetime}
        self._vc_joins: dict[tuple[int, int], datetime] = {}

    # --- VC time tracking ---

    def _scan_existing_vc_users(self) -> None:
        """Record join times for users already in VC when the bot starts."""
        now = datetime.now()
        count = 0
        for guild in self.bot.guilds:
            for vc in guild.voice_channels:
                for member in vc.members:
                    if member.bot:
                        continue
                    key = (guild.id, member.id)
                    if key not in self._vc_joins:
                        self._vc_joins[key] = now
                        count += 1
        if count:
            log.info("Backfilled %d users already in VC", count)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        self._scan_existing_vc_users()

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState,
    ) -> None:
        if member.bot:
            return

        key = (member.guild.id, member.id)
        was_in_vc = before.channel is not None
        now_in_vc = after.channel is not None

        if not was_in_vc and now_in_vc:
            self._vc_joins[key] = datetime.now()
        elif was_in_vc and not now_in_vc:
            self._flush_user(key)

    def _flush_user(self, key: tuple[int, int]) -> None:
        join_time = self._vc_joins.pop(key, None)
        if join_time is None:
            return
        guild_id, user_id = key
        hours = (datetime.now() - join_time).total_seconds() / 3600
        if hours < 0.001:
            return
        self.data.add_vc_time(guild_id, user_id, hours)

    def flush_all(self) -> None:
        """Flush all active VC sessions. Re-records join times so sessions continue."""
        now = datetime.now()
        for key in list(self._vc_joins):
            join_time = self._vc_joins[key]
            guild_id, user_id = key
            hours = (now - join_time).total_seconds() / 3600
            if hours >= 0.001:
                self.data.add_vc_time(guild_id, user_id, hours)
            self._vc_joins[key] = now

    # --- Message tracking ---

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return

        guild_id = message.guild.id

        # Track channel + user message counts
        self.data.add_channel_message(guild_id, message.channel.id)
        self.data.add_messages(guild_id, message.author.id)

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
            if name in ("upvote", "like"):
                upvote = emoji
            elif name in ("downvote", "dislike"):
                downvote = emoji
            if upvote and (downvote or not settings.get("downvote")):
                break

        await message.add_reaction(upvote or EMOJIS["upvote"])
        if settings.get("downvote"):
            await message.add_reaction(downvote or EMOJIS["downvote"])

    # --- Reaction tracking ---

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User) -> None:
        await self._handle_reaction(reaction, user, added=True)

    @commands.Cog.listener()
    async def on_reaction_remove(self, reaction: discord.Reaction, user: discord.User) -> None:
        await self._handle_reaction(reaction, user, added=False)

    async def _handle_reaction(self, reaction: discord.Reaction, user: discord.User, added: bool) -> None:
        if reaction.message.guild is None:
            return
        if user == self.bot.user or user == reaction.message.author:
            return
        if isinstance(reaction.emoji, str):
            return

        guild_id = reaction.message.guild.id
        author_id = reaction.message.author.id
        name = reaction.emoji.name.lower()

        if name in ("upvote", "like", "likeon"):
            karma_delta = 1 if added else -1
        elif name in ("downvote", "dislike", "dislikeon"):
            karma_delta = -1 if added else 1
        else:
            return

        self.data.add_karma(guild_id, author_id, karma_delta)
        self.data.add_friendship(guild_id, user.id, karma_delta)

    # --- Commands ---

    @app_commands.command(name="profile", description="View a user's profile stats")
    @app_commands.describe(user="The user to view (defaults to yourself)")
    async def profile(self, interaction: discord.Interaction, user: discord.User = None) -> None:
        lang = self.data.get_lang(interaction.guild.id)
        target = user or interaction.user
        user_obj = await self.bot.fetch_user(target.id)
        username = user_obj.display_name

        # Flush active VC session so profile shows up-to-date hours
        vc_key = (interaction.guild.id, target.id)
        if vc_key in self._vc_joins:
            self.flush_all()

        totals = self.data.get_profile_totals(interaction.guild.id, target.id)

        # Get accent color from user profile, fall back to extracting from avatar
        accent = user_obj.accent_colour
        if accent is None:
            accent = await self._color_from_avatar(user_obj)

        # Build embed with user's accent color and avatar
        avatar_url = user_obj.display_avatar.url
        # Insert username, capitalize surrounding words but preserve username casing
        raw = lang["ui"]["title"]["profile"].format(username)
        idx = raw.find(username)
        after = raw[idx + len(username):]
        after = " ".join(w[0].upper() + w[1:] if w else w for w in after.split(" "))
        profile_title = raw[:idx] + username + after

        embed = discord.Embed(colour=accent or BOT_COLOR)
        embed.set_author(name=profile_title, icon_url=avatar_url)

        # 2x2 grid using inline fields with spacer
        fields = [
            ("karma", totals["karma"]),
            ("friendship", totals["friendship"]),
            ("messages", totals["messages"]),
            ("vc_hours", totals["vc_time"]),
        ]
        for i, (key, value) in enumerate(fields):
            field_data = lang["ui"]["field"][key]
            embed.add_field(
                name=field_data["title"].title(),
                value=field_data["desc"].format(value),
                inline=True,
            )
            if i % 2 == 0:
                embed.add_field(name="\u200b", value="\u200b", inline=True)

        await interaction.response.send_message(embed=embed)

    async def _color_from_avatar(self, user: discord.User) -> discord.Colour | None:
        """Extract dominant color from user's avatar."""
        try:
            avatar_bytes = await user.display_avatar.read()
            from PIL import Image
            from io import BytesIO
            img = Image.open(BytesIO(avatar_bytes)).convert("RGB").resize((50, 50))
            # Get most common non-dark color
            pixels = list(img.getdata())
            pixels.sort(key=lambda p: sum(p), reverse=True)
            # Pick the pixel at ~25th percentile (avoids pure white/black)
            pick = pixels[len(pixels) // 4]
            return discord.Colour.from_rgb(*pick)
        except Exception:
            return None

    @app_commands.command(name="graph", description="Graph a profile stat over time")
    @app_commands.describe(stat_type="The stat to graph", user="The user to graph")
    @app_commands.choices(stat_type=[
        app_commands.Choice(name="Messages", value="messages"),
        app_commands.Choice(name="Karma", value="karma"),
        app_commands.Choice(name="Friendship", value="friendship"),
        app_commands.Choice(name="VC Time", value="vc_time"),
    ])
    async def graph(self, interaction: discord.Interaction, stat_type: app_commands.Choice[str], user: discord.User) -> None:
        series = self.data.get_stat_series(interaction.guild.id, user.id, stat_type.value)
        if not series:
            await interaction.response.send_message("No data found.", ephemeral=True)
            return

        await interaction.response.defer()
        dates = [row[0] for row in series]
        values = [row[1] for row in series]
        df = DataFrame({"Date": dates, stat_type.name: values})
        fig = px_line(df, x="Date", y=stat_type.name, markers=False, template="seaborn")
        fig["data"][0]["line"]["color"] = "#FF4400"
        fig.write_image("Cache/graph.png")
        await interaction.followup.send(file=discord.File("Cache/graph.png"))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ProfileCog(bot, bot.data))  # type: ignore[attr-defined]
