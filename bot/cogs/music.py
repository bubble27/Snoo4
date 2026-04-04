from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from random import shuffle as random_shuffle
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import BOT_COLOR, ICONS, LOADING_ICON, NP_REFRESH_INTERVAL
from bot.music.embeds import (
    build_nowplaying,
    build_queue_ended,
    build_queued_small,
    build_stopped,
)
from bot.music.player import GuildPlayer
from bot.music.views import NowPlayingView
from bot.utils import extract_yt_id, find_urls, format_time

if TYPE_CHECKING:
    from bot.data import DataManager
    from bot.music.youtube import YouTubeService

log = logging.getLogger(__name__)


class MusicCog(commands.Cog):
    def __init__(self, bot: commands.Bot, data: DataManager, youtube: YouTubeService) -> None:
        self.bot = bot
        self.data = data
        self.yt = youtube
        self.players: dict[int, GuildPlayer] = {}

    def _get_player(self, guild: discord.Guild | discord.Object, channel: discord.abc.Messageable | None = None) -> GuildPlayer:
        guild_id = guild.id
        if guild_id not in self.players:
            self.players[guild_id] = GuildPlayer(guild_id=guild_id, channel=channel)
        elif channel is not None:
            self.players[guild_id].channel = channel
        return self.players[guild_id]

    def _build_view(self, player: GuildPlayer) -> NowPlayingView:
        liked = False
        if player.guild_id in self.data.playlists:
            liked_list = self.data.playlists[player.guild_id].get("liked", [])
            liked = player.current in liked_list

        return NowPlayingView(
            player, liked,
            on_like=self._make_like_cb(player),
            on_back=self._make_back_cb(player),
            on_pause=self._make_pause_cb(player),
            on_skip=self._make_skip_cb(player),
            on_toggle_queue=self._make_queue_toggle_cb(player),
            on_stop=self._make_stop_cb(player),
        )

    # --- Slash Commands ---

    @app_commands.command(name="play", description="Play a song or playlist from YouTube")
    @app_commands.describe(search="YouTube URL or search query")
    async def play(self, interaction: discord.Interaction, search: str = None) -> None:
        await interaction.response.defer()
        await self._play_sys(interaction.guild, interaction.channel, None, interaction.user, search, respond=interaction)

    @app_commands.command(name="stop", description="Stop playback and disconnect")
    async def stop(self, interaction: discord.Interaction) -> None:
        lang = self.data.get_lang(interaction.guild.id)
        player = self.players.get(interaction.guild.id)
        if player and player.queue:
            await interaction.response.defer()
            await self._stop_player(player)
            await interaction.followup.send(embed=build_stopped(lang))
        else:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)

    @app_commands.command(name="pause", description="Pause or resume playback")
    async def pause(self, interaction: discord.Interaction) -> None:
        player = self.players.get(interaction.guild.id)
        if player is None:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        if not player.paused:
            player.pause()
            await interaction.response.send_message("\u23f8\ufe0f Paused", ephemeral=True)
        else:
            player.resume()
            await interaction.response.send_message("\u25b6\ufe0f Resumed", ephemeral=True)

    @app_commands.command(name="skip", description="Skip to the next song")
    async def skip(self, interaction: discord.Interaction) -> None:
        lang = self.data.get_lang(interaction.guild.id)
        player = self.players.get(interaction.guild.id)
        if player is None:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        if len(player.queue) > 1 or player.autoplay:
            await interaction.response.defer()
            if player.paused:
                player.resume()
            await self._play_next(player)
            await interaction.followup.send("\u23ed\ufe0f Skipped", ephemeral=True)
        else:
            await interaction.response.send_message(lang["error"]["can_not_skip"], ephemeral=True)

    @app_commands.command(name="back", description="Go back to the previous song")
    async def back(self, interaction: discord.Interaction) -> None:
        lang = self.data.get_lang(interaction.guild.id)
        player = self.players.get(interaction.guild.id)
        if player is None:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        if player.past_queue:
            await interaction.response.defer()
            if player.paused:
                player.resume()
            cur = player.queue[0]
            player.queue.insert(1, player.past_queue[-1])
            await self._play_next(player)
            player.queue.insert(1, cur)
            del player.past_queue[-2:]
            await interaction.followup.send("\u23ee\ufe0f Rewound", ephemeral=True)
        else:
            await interaction.response.send_message(lang["error"]["can_not_back"], ephemeral=True)

    @app_commands.command(name="loop", description="Toggle looping the current song")
    async def loop(self, interaction: discord.Interaction) -> None:
        lang = self.data.get_lang(interaction.guild.id)
        player = self.players.get(interaction.guild.id)
        if player and player.is_playing:
            player.looping = not player.looping
            key = "loop_start" if player.looping else "loop_stop"
            await interaction.response.send_message(lang["notifs"][key])
        else:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)

    @app_commands.command(name="autoplay", description="Toggle autoplay mode")
    async def autoplay(self, interaction: discord.Interaction) -> None:
        lang = self.data.get_lang(interaction.guild.id)
        player = self.players.get(interaction.guild.id)
        if player and player.is_playing:
            player.autoplay = not player.autoplay
            key = "autoplay_start" if player.autoplay else "autoplay_stop"
            await interaction.response.send_message(lang["notifs"][key])
        else:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)

    @app_commands.command(name="shuffle", description="Shuffle or unshuffle the queue")
    async def shuffle_cmd(self, interaction: discord.Interaction) -> None:
        lang = self.data.get_lang(interaction.guild.id)
        player = self.players.get(interaction.guild.id)
        if player is None or not player.is_playing:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        if not player.shuffle:
            player.original_queue = list(player.queue)
            current = player.queue[0]
            rest = player.queue[1:]
            random_shuffle(rest)
            player.queue = [current] + rest
            player.shuffle = True
            await interaction.response.send_message(lang["notifs"]["shuffle"])
        else:
            if player.original_queue:
                player.queue = player.original_queue
            player.shuffle = False
            player.original_queue = None
            await interaction.response.send_message(lang["notifs"]["unshuffle"])

    @app_commands.command(name="nowplaying", description="Re-send the now playing display")
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        player = self.players.get(interaction.guild.id)
        if player is None or not player.current:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self._refresh_display(player, new_channel=interaction.channel)
        await interaction.followup.send("Updated!", ephemeral=True)

    # --- Core play system ---

    async def _play_sys(
        self,
        guild: discord.Guild,
        channel,
        reference=None,
        user=None,
        search: str | None = None,
        autoplay_id: str | None = None,
        respond: discord.Interaction | None = None,
    ) -> None:
        self.data.verify_settings(guild.id)
        player = self._get_player(guild, channel)

        # Ensure liked playlist exists
        if guild.id not in self.data.playlists or "liked" not in self.data.playlists[guild.id]:
            self.data.playlists[guild.id]["liked"] = []

        lang = self.data.get_lang(guild.id)
        video_id = None
        playlist = None
        searching_msg = None

        if autoplay_id is not None:
            video_id = autoplay_id
        else:
            if user is not None and not hasattr(user, "voice"):
                pass
            elif user is not None and user.voice is None:
                if respond:
                    await respond.followup.send(lang["error"]["no_vc"], ephemeral=True)
                else:
                    await channel.send(lang["error"]["no_vc"])
                return

            video_id, playlist, search = await self._resolve_input(reference, channel, search, lang)
            if video_id is None and playlist is None and search is None:
                return

            if video_id is None:
                if playlist is None:
                    playlist = self._search_to_playlist(search, guild.id)

                if playlist is not None:
                    if not player.queue:
                        search = playlist[0]
                        playlist = playlist[1:]
                    else:
                        video_id = "__skip__"

                if video_id != "__skip__":
                    search_text = f'{lang["notifs"]["searching"].format(search)}   {LOADING_ICON}'
                    if respond:
                        searching_msg = await respond.followup.send(search_text, wait=True)
                    else:
                        searching_msg = await channel.send(search_text)
                    video_id = self.yt.search_and_fetch(search)
                    if video_id is None:
                        await searching_msg.edit(content=lang["error"]["nothing_found"])
                        return

        if video_id != "__skip__":
            if not self.yt.fetch_info(video_id):
                err = lang["error"]["play_error"]
                if searching_msg:
                    await searching_msg.edit(content=err)
                elif respond:
                    await respond.followup.send(err, ephemeral=True)
                else:
                    await channel.send(err)
                return

            player.queue.append(video_id)
            player.refetch_tried = False

            if autoplay_id is None:
                if len(player.queue) == 1:
                    if user is not None and hasattr(user, "voice") and user.voice:
                        await player.connect(user.voice.channel)
                    source_url = self.yt.cache[video_id].get("source", "")
                    await player.play(source_url)

                    await self._send_display(player)

                    if searching_msg:
                        await searching_msg.delete()

                    player.update_count = 0
                    player.update_task = asyncio.create_task(self._update_loop(player))
                else:
                    embed = build_queued_small(video_id, self.yt.cache, lang)
                    if searching_msg:
                        await searching_msg.edit(content="", embed=embed)
                    else:
                        await channel.send(embed=embed)
            else:
                source_url = self.yt.cache[video_id].get("source", "")
                await player.play(source_url)

            self.yt.find_autoplay(player)

        if playlist:
            await self._queue_playlist(channel, player, playlist, lang)

    async def _resolve_input(self, reference, channel, search, lang) -> tuple:
        if reference is None:
            if search is None:
                await channel.send(lang["error"]["no_content"])
                return None, None, None
            from validators import url as is_url
            if is_url(search) and "youtube" in search and "list=" in search:
                playlist = self._fetch_playlist_urls(search)
                return None, playlist, search
            yt_id = extract_yt_id(search)
            if self.yt.verify_id(yt_id):
                return yt_id, None, search
            return None, None, search

        try:
            msg = await channel.fetch_message(reference.message_id)
        except Exception:
            msg = reference

        urls = find_urls(msg.content) if hasattr(msg, "content") else []
        if urls:
            url = urls[0]
            if "youtube" in url and "list=" in url:
                return None, self._fetch_playlist_urls(url), None
            yt_id = extract_yt_id(url)
            if self.yt.verify_id(yt_id):
                return yt_id, None, None
            return None, None, msg.content

        if hasattr(msg, "embeds") and msg.embeds:
            embed_url = str(msg.embeds[0].url)
            if embed_url and embed_url != "Embed.Empty":
                yt_id = extract_yt_id(embed_url)
                if self.yt.verify_id(yt_id):
                    return yt_id, None, None
                await channel.send(lang["error"]["not_youtube"])
                return None, None, None
            await channel.send(lang["error"]["no_content"])
            return None, None, None

        if hasattr(msg, "content") and msg.content:
            return None, None, msg.content

        await channel.send(lang["error"]["no_content"])
        return None, None, None

    def _fetch_playlist_urls(self, playlist_url: str) -> list[str]:
        try:
            from pytube import Playlist
            return [extract_yt_id(url) for url in Playlist(playlist_url).video_urls]
        except Exception:
            log.warning("Failed to fetch playlist: %s", playlist_url)
            return []

    def _search_to_playlist(self, search: str | None, guild_id: int) -> list[str] | None:
        if search is None:
            return None
        if search.startswith("[") and search.endswith("]"):
            return search.strip("[]").split(", ")
        if search in self.data.playlists.get(guild_id, {}):
            return self.data.playlists[guild_id][search].get("songs", [])
        return None

    async def _queue_playlist(self, channel, player, playlist, lang) -> None:
        embed = discord.Embed(color=BOT_COLOR)
        embed.set_author(
            name=f'{lang["ui"]["title"]["queued"].title()} 0 / {len(playlist)}',
            icon_url=ICONS["music"],
        )
        msg = await channel.send(embed=embed)

        total_time = 0
        for i, video_id in enumerate(playlist):
            attempts = 0
            while video_id not in self.yt.cache and attempts < 60:
                await asyncio.sleep(0.5)
                attempts += 1

            if video_id in self.yt.cache:
                player.queue.append(video_id)
                total_time += self.yt.cache[video_id].get("secs_length", 0)

            if (i + 1) % 5 == 0 or i == len(playlist) - 1:
                progress_embed = discord.Embed(color=BOT_COLOR)
                progress_embed.set_author(
                    name=f'{lang["ui"]["title"]["queued"].title()} {i + 1} / {len(playlist)}',
                    icon_url=ICONS["music"],
                )
                if playlist[0] in self.yt.cache:
                    progress_embed.set_thumbnail(url=self.yt.cache[playlist[0]].get("thumbnail", ""))
                progress_embed.set_footer(text=lang["ui"]["field"]["queue_footer"]["short"].format(format_time(total_time)))
                await msg.edit(embed=progress_embed)

        final_embed = discord.Embed(color=BOT_COLOR)
        final_embed.set_author(name=lang["ui"]["title"]["queued"].upper(), icon_url=ICONS["music"])
        if playlist and playlist[0] in self.yt.cache:
            final_embed.set_thumbnail(url=self.yt.cache[playlist[0]].get("thumbnail", ""))
        final_embed.set_footer(text=lang["ui"]["field"]["queue_footer"]["short"].format(format_time(total_time)))
        await msg.edit(embed=final_embed)

        if playlist:
            self.yt.find_autoplay(player)

    # --- Playback control ---

    async def _play_next(self, player: GuildPlayer) -> None:
        current_id = player.current
        if current_id is None:
            return

        player.past_queue.append(current_id)
        if not player.looping:
            del player.queue[0]

        self._record_history(player, current_id)

        if player.queue:
            source = self.yt.cache.get(player.queue[0], {}).get("source", "")
            await player.play(source)
        elif player.autoplay:
            if not player.recommended_vid:
                self.yt.find_autoplay(player)
            if player.recommended_vid:
                rec = player.recommended_vid
                player.recommended_vid = None
                await self._play_sys(
                    discord.Object(id=player.guild_id),
                    player.channel,
                    autoplay_id=rec,
                )
            else:
                lang = self.data.get_lang(player.guild_id)
                await player.disconnect()
                player.cancel_update_task()
                await player.channel.send(embed=build_queue_ended(lang))
                player.queue.clear()
                return
        else:
            lang = self.data.get_lang(player.guild_id)
            await player.disconnect()
            player.cancel_update_task()
            await player.channel.send(embed=build_queue_ended(lang))
            player.queue.clear()
            return

        if player.current:
            await self._update_display(player)

    def _record_history(self, player: GuildPlayer, video_id: str) -> None:
        elapsed = player.elapsed_seconds()
        duration = self.yt.cache.get(video_id, {}).get("secs_length", 0)
        retention = round(elapsed / duration, 2) if duration > 0 else 0.0

        guild_id = player.guild_id
        for vc in self.bot.guilds:
            if vc.id != guild_id:
                continue
            for voice_ch in vc.voice_channels:
                for member in voice_ch.members:
                    if member.bot:
                        continue
                    user_id = member.id
                    hist = self.data.song_history
                    if guild_id not in hist or user_id not in hist[guild_id]:
                        hist[guild_id][user_id] = [{video_id: [{"retention": retention, "listen_time": int(elapsed)}]}]
                    elif video_id not in hist[guild_id][user_id][-1]:
                        hist[guild_id][user_id][-1][video_id] = [{"retention": retention, "listen_time": int(elapsed)}]
                    else:
                        hist[guild_id][user_id][-1][video_id].append({"retention": retention, "listen_time": int(elapsed)})

    async def _stop_player(self, player: GuildPlayer) -> None:
        player.cancel_update_task()
        await player.disconnect()
        self.players.pop(player.guild_id, None)

    # --- Display ---

    async def _send_display(self, player: GuildPlayer) -> None:
        embeds = build_nowplaying(player, self.yt.cache, self.data.get_lang(player.guild_id))
        if not embeds:
            return
        view = self._build_view(player)
        player.thumbnail_msg = await player.channel.send(embed=embeds[1])
        player.nowplaying_msg = await player.channel.send(embed=embeds[0])
        player.button_msg = await player.channel.send(embed=embeds[2], view=view)

    async def _update_display(self, player: GuildPlayer) -> None:
        embeds = build_nowplaying(player, self.yt.cache, self.data.get_lang(player.guild_id))
        if not embeds:
            return
        try:
            if player.thumbnail_msg:
                await player.thumbnail_msg.edit(embed=embeds[1])
            if player.button_msg:
                await player.button_msg.edit(embed=embeds[2])
        except discord.NotFound:
            pass

    async def _refresh_display(self, player: GuildPlayer, new_channel=None) -> None:
        if new_channel:
            player.channel = new_channel
        embeds = build_nowplaying(player, self.yt.cache, self.data.get_lang(player.guild_id))
        if not embeds:
            return
        view = self._build_view(player)

        for msg in (player.thumbnail_msg, player.nowplaying_msg, player.button_msg):
            if msg:
                try:
                    await msg.delete()
                except discord.NotFound:
                    pass

        player.thumbnail_msg = await player.channel.send(embed=embeds[1])
        player.nowplaying_msg = await player.channel.send(embed=embeds[0])
        player.button_msg = await player.channel.send(embed=embeds[2], view=view)

    # --- Update loop ---

    async def _update_loop(self, player: GuildPlayer) -> None:
        while True:
            await asyncio.sleep(1)
            try:
                if player.paused or player.processing or not player.queue:
                    continue

                if player.is_playing:
                    embeds = build_nowplaying(player, self.yt.cache, self.data.get_lang(player.guild_id))
                    if embeds and player.nowplaying_msg:
                        try:
                            await player.nowplaying_msg.edit(embed=embeds[0])
                        except discord.NotFound:
                            pass
                else:
                    ended = await self._check_song_ended(player)
                    if not ended and not player.refetch_tried:
                        log.info("Refetching video source...")
                        try:
                            if self.yt.fetch_info(player.current, only_source=True, refetch=True):
                                source = self.yt.cache[player.current].get("source", "")
                                await player.play(source)
                                log.info("Refetch successful")
                        except Exception as e:
                            log.warning("Refetch failed: %r", e)
                        finally:
                            player.refetch_tried = True

                player.update_count += 1
                if player.update_count >= NP_REFRESH_INTERVAL:
                    player.update_count = 0
                    await self._refresh_display(player)

            except asyncio.CancelledError:
                return
            except Exception:
                log.error("Update loop error", exc_info=True)

    async def _check_song_ended(self, player: GuildPlayer) -> bool:
        if not player.queue:
            return True

        video_id = player.current
        duration = self.yt.cache.get(video_id, {}).get("secs_length", 0)
        elapsed = player.elapsed_seconds()

        if duration <= 0:
            if elapsed < 5:
                return False
        elif elapsed + 1 < duration:
            return False

        if len(player.queue) > 1 or player.looping or player.autoplay:
            await self._play_next(player)
        else:
            lang = self.data.get_lang(player.guild_id)
            await player.disconnect()
            player.cancel_update_task()
            await player.channel.send(embed=build_queue_ended(lang))
            player.queue.clear()

        return True

    # --- Button callbacks ---

    def _make_like_cb(self, player: GuildPlayer):
        async def callback(interaction: discord.Interaction):
            guild_id = interaction.guild.id
            if guild_id not in self.data.playlists:
                self.data.playlists[guild_id] = {}
            liked = self.data.playlists[guild_id].setdefault("liked", [])
            current = player.current
            if current in liked:
                liked.remove(current)
            else:
                liked.append(current)
            embeds = build_nowplaying(player, self.yt.cache, self.data.get_lang(player.guild_id))
            view = self._build_view(player)
            if embeds:
                await interaction.response.edit_message(embed=embeds[2], view=view)
            else:
                await interaction.response.defer()
        return callback

    def _make_back_cb(self, player: GuildPlayer):
        async def callback(interaction: discord.Interaction):
            lang = self.data.get_lang(interaction.guild.id)
            if player.past_queue:
                await interaction.response.defer()
                if player.paused:
                    player.resume()
                cur = player.queue[0]
                player.queue.insert(1, player.past_queue[-1])
                await self._play_next(player)
                player.queue.insert(1, cur)
                del player.past_queue[-2:]
            else:
                await interaction.response.send_message(lang["error"]["can_not_back"], ephemeral=True)
        return callback

    def _make_pause_cb(self, player: GuildPlayer):
        async def callback(interaction: discord.Interaction):
            if not player.paused:
                player.pause()
            else:
                player.resume()
            embeds = build_nowplaying(player, self.yt.cache, self.data.get_lang(player.guild_id))
            view = self._build_view(player)
            if embeds:
                await interaction.response.edit_message(embed=embeds[2], view=view)
            else:
                await interaction.response.defer()
        return callback

    def _make_skip_cb(self, player: GuildPlayer):
        async def callback(interaction: discord.Interaction):
            lang = self.data.get_lang(interaction.guild.id)
            if len(player.queue) > 1 or player.autoplay:
                await interaction.response.defer()
                if player.paused:
                    player.resume()
                await self._play_next(player)
            else:
                await interaction.response.send_message(lang["error"]["can_not_skip"], ephemeral=True)
        return callback

    def _make_queue_toggle_cb(self, player: GuildPlayer):
        async def callback(interaction: discord.Interaction):
            player.show_queue = not player.show_queue
            embeds = build_nowplaying(player, self.yt.cache, self.data.get_lang(player.guild_id))
            view = self._build_view(player)
            if embeds:
                await interaction.response.edit_message(embed=embeds[2], view=view)
            else:
                await interaction.response.defer()
        return callback

    def _make_stop_cb(self, player: GuildPlayer):
        async def callback(interaction: discord.Interaction):
            await interaction.response.defer()
            lang = self.data.get_lang(interaction.guild.id)
            await self._stop_player(player)
            await interaction.followup.send(embed=build_stopped(lang))
        return callback


async def setup(bot: commands.Bot) -> None:
    data = bot.data  # type: ignore[attr-defined]
    youtube = bot.youtube  # type: ignore[attr-defined]
    await bot.add_cog(MusicCog(bot, data, youtube))
