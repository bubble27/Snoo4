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
    build_info_view,
    build_controls_view,
    build_queue_ended,
    build_queued_small,
    build_stopped,
)
from bot.music.player import GuildPlayer
from bot.music.views import build_nowplaying_buttons
from bot.utils import extract_yt_id, find_urls, format_time, truncate

if TYPE_CHECKING:
    from bot.data import DataManager
    from bot.music.youtube import YouTubeService

log = logging.getLogger(__name__)


def _extract_component_text(components) -> str:
    """Recursively extract text content from Components V2 structures."""
    texts = []
    for comp in components:
        if hasattr(comp, "content"):
            texts.append(comp.content)
        if hasattr(comp, "children"):
            texts.append(_extract_component_text(comp.children))
        if hasattr(comp, "components"):
            texts.append(_extract_component_text(comp.components))
    return " ".join(texts)


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

    def _build_buttons(self, player: GuildPlayer) -> list:
        playlists = self.data.playlists.get(player.guild_id, {})
        liked = player.current in playlists.get("liked", [])
        disliked = player.current in playlists.get("disliked", [])

        return build_nowplaying_buttons(
            player, liked, disliked,
            on_like=self._make_like_cb(player),
            on_back=self._make_back_cb(player),
            on_pause=self._make_pause_cb(player),
            on_skip=self._make_skip_cb(player),
            on_toggle_queue=self._make_queue_toggle_cb(player),
            on_dislike=self._make_dislike_cb(player),
            on_loop=self._make_loop_cb(player),
            on_shuffle=self._make_shuffle_cb(player),
            on_autoplay=self._make_autoplay_cb(player),
            on_stop=self._make_stop_cb(player),
        )

    def _build_info(self, player: GuildPlayer):
        lang = self.data.get_lang(player.guild_id)
        return build_info_view(player, self.yt.cache, lang)

    def _build_controls(self, player: GuildPlayer):
        buttons = self._build_buttons(player)
        lang = self.data.get_lang(player.guild_id)
        return build_controls_view(player, self.yt.cache, lang, buttons)

    # --- Slash Commands ---

    @app_commands.command(name="play", description="Play a song or playlist from YouTube")
    @app_commands.describe(search="YouTube URL or search query")
    async def play(self, interaction: discord.Interaction, search: str = None) -> None:
        try:
            await interaction.response.defer()
        except discord.NotFound:
            return  # Interaction expired (e.g. during startup sync)
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
            # Save original order of unplayed songs for unshuffle
            player.original_queue = list(player.queue)
            current = player.queue[0]
            rest = player.queue[1:]
            random_shuffle(rest)
            player.queue = [current] + rest
            player.shuffle = True
            await interaction.response.send_message(lang["notifs"]["shuffle"])
        else:
            # Restore original order, but only for songs still in queue (skip already-played ones)
            if player.original_queue:
                remaining = set(player.queue)
                restored = [v for v in player.original_queue if v in remaining]
                # Keep current song at position 0
                if player.current and player.current in restored:
                    restored.remove(player.current)
                    restored.insert(0, player.current)
                player.queue = restored
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

    # --- Context menu (right-click message → play) ---

    async def _play_from_message(self, interaction: discord.Interaction, message: discord.Message) -> None:
        """Right-click a message to parse and play its content (URLs, embeds, or text)."""
        await interaction.response.defer()
        try:
            await self._play_sys(
                interaction.guild, interaction.channel,
                reference=message, user=interaction.user,
                respond=interaction,
            )
        except Exception as e:
            try:
                await interaction.followup.send("Something went wrong.", ephemeral=True)
            except Exception:
                pass
            log.error("Context menu play error: %s", e, exc_info=True)

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
                    if playlist:
                        log.info("Playlist from search_to_playlist: %s (from search=%r)", playlist[:5], search[:100] if search else search)

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
                    video_id = self.yt.search_and_fetch(search, disliked=self._get_disliked(guild.id))
                    if video_id is None:
                        await searching_msg.edit(content=lang["error"]["nothing_found"])
                        return

        if video_id != "__skip__":
            # Show fetching message for direct URLs (no search was done)
            status_msg = searching_msg
            if status_msg is None and respond and autoplay_id is None:
                fetching_text = f'{lang["notifs"]["fetching"]}   {LOADING_ICON}'
                status_msg = await respond.followup.send(fetching_text, wait=True)

            if not self.yt.fetch_info(video_id):
                err = lang["error"]["play_error"]
                if status_msg:
                    await status_msg.edit(content=err)
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

                    if status_msg:
                        try:
                            await status_msg.delete()
                        except discord.NotFound:
                            pass

                    player.update_count = 0
                    player.update_task = asyncio.create_task(self._update_loop(player))
                else:
                    embed = build_queued_small(video_id, self.yt.cache, lang)
                    if status_msg:
                        await status_msg.edit(content="", embed=embed)
                    else:
                        await channel.send(embed=embed)
            else:
                source_url = self.yt.cache[video_id].get("source", "")
                await player.play(source_url)

            self.yt.find_autoplay(player, disliked=self._get_disliked(player.guild_id))

        if playlist:
            await self._queue_playlist(channel, player, playlist, lang)

    async def _resolve_input(self, reference, channel, search, lang) -> tuple:
        if reference is None:
            if search is None:
                await channel.send(lang["error"]["no_content"])
                return None, None, None
            from validators import url as is_url
            if is_url(search) and "youtube" in search and "list=" in search:
                playlist = self.yt.fetch_playlist(search)
                return None, playlist, search
            yt_id = extract_yt_id(search)
            if self.yt.verify_id(yt_id):
                return yt_id, None, search
            return None, None, search

        # reference can be a Message (context menu) or MessageReference (reply)
        if isinstance(reference, discord.Message):
            msg = reference
        else:
            try:
                msg = await channel.fetch_message(reference.message_id)
            except Exception:
                msg = reference

        # Extract URLs from message content, embeds, or Components V2 text
        text_to_search = ""
        if hasattr(msg, "content") and msg.content:
            text_to_search = msg.content
        if hasattr(msg, "components") and msg.components:
            text_to_search += " " + _extract_component_text(msg.components)

        urls = find_urls(text_to_search)
        if urls:
            url = urls[0]
            if "youtube" in url and "list=" in url:
                return None, self.yt.fetch_playlist(url), None
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

    def _search_to_playlist(self, search: str | None, guild_id: int) -> list[str] | None:
        if search is None:
            return None
        # Match bracket-delimited lists like [song1, url, song2]
        # Each item can be a video ID, URL, or search query — resolved later
        if search.startswith("[") and search.endswith("]"):
            items = [s.strip() for s in search[1:-1].split(",") if s.strip()]
            # Extract YouTube IDs from URLs, leave search queries as-is
            resolved = []
            for item in items:
                yt_id = extract_yt_id(item)
                resolved.append(yt_id if yt_id != item or len(item) == 11 else item)
            if resolved:
                return resolved
        if search in self.data.playlists.get(guild_id, {}):
            return self.data.playlists[guild_id][search].get("songs", [])
        return None

    async def _queue_playlist(self, channel, player, playlist_ids: list[str], lang) -> None:
        """Fetch and queue a list of items concurrently. Items can be video IDs or search queries."""
        from concurrent.futures import Future
        from bot.music.youtube import _pool

        disliked = self._get_disliked(player.guild_id)
        total = len(playlist_ids)
        embed = discord.Embed(color=BOT_COLOR)
        embed.set_author(
            name=f'{lang["ui"]["title"]["queued"].title()} 0 / {total}',
            icon_url=ICONS["music"],
        )
        msg = await channel.send(embed=embed)

        # Submit ALL fetches at once — search_and_fetch handles both IDs and queries
        futures: list[tuple[int, str, Future]] = [
            (i, item, _pool.submit(self.yt.search_and_fetch, item, disliked))
            for i, item in enumerate(playlist_ids)
        ]

        # Pre-allocate slots to preserve original order
        slots: list[str | None] = [None] * total
        queued_display: list[str] = []  # ordered list for display
        total_time = 0
        first_thumb = None
        queue_insert_base = len(player.queue)  # where this batch starts in the queue

        # Poll futures, inserting in order as they complete
        pending = list(futures)
        failed = 0
        changed = False
        while pending:
            still_pending = []
            changed = False
            for idx, item, future in pending:
                if future.done():
                    try:
                        resolved_id = future.result()
                        if resolved_id:
                            slots[idx] = resolved_id
                            changed = True
                        else:
                            failed += 1
                            changed = True
                            log.warning("No result for %s", item)
                    except Exception:
                        failed += 1
                        changed = True
                        log.warning("Failed to fetch/search for %s", item)
                else:
                    still_pending.append((idx, item, future))

            if changed:
                # Rebuild the queue portion in original order
                ordered = [s for s in slots if s is not None]
                del player.queue[queue_insert_base:]
                player.queue.extend(ordered)

                queued_display = list(ordered)
                total_time = sum(self.yt.cache.get(v, {}).get("secs_length", 0) for v in ordered)
                if not first_thumb and ordered:
                    first_thumb = self.yt.cache.get(ordered[0], {}).get("thumbnail", "")

                progress = self._build_queue_progress(
                    lang, queued_display, total, total_time, first_thumb, done=not still_pending,
                )
                try:
                    await msg.edit(embed=progress)
                except discord.NotFound:
                    pass

            pending = still_pending
            if pending:
                await asyncio.sleep(0.3)

        self.yt.find_autoplay(player, disliked=self._get_disliked(player.guild_id))

    def _build_queue_progress(
        self, lang: dict, songs: list[str], total: int,
        total_time: int, thumb: str | None, done: bool,
    ) -> discord.Embed:
        count = len(songs)
        embed = discord.Embed(color=BOT_COLOR)

        if done:
            title = f'{lang["ui"]["title"]["queued"].upper()} — {count} / {total}'
        else:
            title = f'{lang["ui"]["title"]["queued"].title()} {count} / {total}'

        embed.set_author(name=title, icon_url=ICONS["music"])

        if thumb:
            embed.set_thumbnail(url=thumb)

        # Two-column layout matching the queue embed: song names | durations
        visible = songs[-15:] if len(songs) > 15 else songs
        start_num = len(songs) - len(visible) + 1
        names_col = ""
        dur_col = ""
        for i, vid_id in enumerate(visible):
            vid = self.yt.cache.get(vid_id, {})
            name = truncate(vid.get("title", "Unknown"))
            names_col += f"**{start_num + i}** {name}\n"
            dur_col += format_time(vid.get("secs_length", 0)) + "\n"

        if names_col:
            embed.add_field(
                name=lang["ui"]["field"]["next_up"].title(),
                value=names_col, inline=True,
            )
            embed.add_field(
                name=lang["ui"]["field"]["duration"].title(),
                value=dur_col, inline=True,
            )

        if len(songs) > 15:
            embed.set_footer(
                text=lang["ui"]["field"]["queue_footer"]["full"].format(
                    len(songs) - 15, format_time(total_time)
                )
            )
        else:
            embed.set_footer(
                text=lang["ui"]["field"]["queue_footer"]["short"].format(format_time(total_time))
            )
        return embed

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
                self.yt.find_autoplay(player, disliked=self._get_disliked(player.guild_id))
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
        for guild in self.bot.guilds:
            if guild.id != guild_id:
                continue
            for voice_ch in guild.voice_channels:
                for member in voice_ch.members:
                    if member.bot:
                        continue
                    self.data.record_listen(
                        guild_id, member.id, video_id, retention, int(elapsed),
                    )

    async def _stop_player(self, player: GuildPlayer) -> None:
        player.cancel_update_task()
        await player.disconnect()
        self.players.pop(player.guild_id, None)

    # --- Display ---

    async def _send_display(self, player: GuildPlayer) -> None:
        info = self._build_info(player)
        controls = self._build_controls(player)
        if not info or not controls:
            return
        for attempt in range(3):
            try:
                player.display_msg = await player.channel.send(view=info)
                player.controls_msg = await player.channel.send(view=controls)
                return
            except discord.DiscordServerError:
                if attempt < 2:
                    await asyncio.sleep(1)
                else:
                    log.error("Failed to send now-playing display after 3 attempts")

    async def _update_info(self, player: GuildPlayer) -> None:
        """Update the info container (every second). Never touches buttons."""
        info = self._build_info(player)
        if not info or not player.display_msg:
            return
        try:
            await player.display_msg.edit(view=info)
        except (discord.NotFound, discord.HTTPException):
            pass

    async def _update_controls(self, player: GuildPlayer) -> None:
        """Update the controls container. Only on song change or user action."""
        controls = self._build_controls(player)
        if not controls or not player.controls_msg:
            return
        try:
            await player.controls_msg.edit(view=controls)
        except (discord.NotFound, discord.HTTPException):
            pass

    async def _update_display(self, player: GuildPlayer) -> None:
        """Update both (song change)."""
        await self._update_info(player)
        await self._update_controls(player)

    async def _refresh_display(self, player: GuildPlayer, new_channel=None) -> None:
        if new_channel:
            player.channel = new_channel
        for msg in (player.display_msg, player.controls_msg):
            if msg:
                try:
                    await msg.delete()
                except discord.NotFound:
                    pass
        await self._send_display(player)

    # --- Update loop ---

    async def _update_loop(self, player: GuildPlayer) -> None:
        ticks = 0
        while True:
            try:
                if player.paused or player.processing or not player.queue:
                    await asyncio.sleep(1)
                    continue

                ticks += 1

                if player.is_playing:
                    await self._update_info(player)
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

                if ticks >= NP_REFRESH_INTERVAL:
                    ticks = 0
                    await self._refresh_display(player)

            except asyncio.CancelledError:
                return
            except Exception:
                log.error("Update loop error", exc_info=True)

            await asyncio.sleep(1)

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

    # --- Helpers ---

    def _get_disliked(self, guild_id: int) -> set[str]:
        return set(self.data.playlists.get(guild_id, {}).get("disliked", []))

    # --- Button callbacks ---

    def _is_stale(self, player: GuildPlayer) -> bool:
        """Check if a player reference is stale (bot restarted or stopped)."""
        return player.guild_id not in self.players or self.players[player.guild_id] is not player

    async def _respond_with_controls(self, interaction: discord.Interaction, player: GuildPlayer) -> None:
        """Respond to interaction by editing the controls container."""
        controls = self._build_controls(player)
        if controls:
            try:
                await interaction.response.edit_message(view=controls)
            except discord.HTTPException:
                await interaction.response.defer()
        else:
            await interaction.response.defer()

    def _make_like_cb(self, player: GuildPlayer):
        async def callback(interaction: discord.Interaction):
            if self._is_stale(player) or not player.current:
                await interaction.response.send_message(self.data.get_lang(interaction.guild.id)["error"]["session_expired"], ephemeral=True)
                return
            guild_id = interaction.guild.id
            if guild_id not in self.data.playlists:
                self.data.playlists[guild_id] = {}
            liked = self.data.playlists[guild_id].setdefault("liked", [])
            current = player.current
            if current in liked:
                liked.remove(current)
            else:
                liked.append(current)
            await self._respond_with_controls(interaction, player)
        return callback

    def _make_dislike_cb(self, player: GuildPlayer):
        async def callback(interaction: discord.Interaction):
            if self._is_stale(player) or not player.current:
                await interaction.response.send_message(self.data.get_lang(interaction.guild.id)["error"]["session_expired"], ephemeral=True)
                return
            guild_id = interaction.guild.id
            if guild_id not in self.data.playlists:
                self.data.playlists[guild_id] = {}
            disliked = self.data.playlists[guild_id].setdefault("disliked", [])
            current = player.current
            if current not in disliked:
                disliked.append(current)
            # Also remove from liked if it was there
            liked = self.data.playlists[guild_id].get("liked", [])
            if current in liked:
                liked.remove(current)
            # Remove all occurrences from queue
            player.queue = [v for v in player.queue if v != current]
            # Skip to next
            await interaction.response.defer()
            try:
                if player.queue:
                    source = self.yt.cache.get(player.queue[0], {}).get("source", "")
                    await player.play(source)
                elif player.autoplay:
                    if not player.recommended_vid:
                        self.yt.find_autoplay(player, disliked=self._get_disliked(guild_id))
                    if player.recommended_vid:
                        rec = player.recommended_vid
                        player.recommended_vid = None
                        await self._play_sys(discord.Object(id=guild_id), player.channel, autoplay_id=rec)
                    else:
                        lang = self.data.get_lang(guild_id)
                        await player.disconnect()
                        player.cancel_update_task()
                        await player.channel.send(embed=build_queue_ended(lang))
                else:
                    lang = self.data.get_lang(guild_id)
                    await player.disconnect()
                    player.cancel_update_task()
                    await player.channel.send(embed=build_queue_ended(lang))
            except Exception:
                log.error("Button callback error", exc_info=True)
        return callback

    def _make_back_cb(self, player: GuildPlayer):
        async def callback(interaction: discord.Interaction):
            if self._is_stale(player):
                await interaction.response.send_message(self.data.get_lang(interaction.guild.id)["error"]["session_expired"], ephemeral=True)
                return
            lang = self.data.get_lang(interaction.guild.id)
            if player.past_queue:
                player.interaction_active = True
                try:
                    await interaction.response.defer()
                    if player.paused:
                        player.resume()
                    cur = player.queue[0]
                    player.queue.insert(1, player.past_queue[-1])
                    await self._play_next(player)
                    player.queue.insert(1, cur)
                    del player.past_queue[-2:]
                finally:
                    player.interaction_active = False
            else:
                await interaction.response.send_message(lang["error"]["can_not_back"], ephemeral=True)
        return callback

    def _make_pause_cb(self, player: GuildPlayer):
        async def callback(interaction: discord.Interaction):
            if self._is_stale(player):
                await interaction.response.send_message(self.data.get_lang(interaction.guild.id)["error"]["session_expired"], ephemeral=True)
                return
            if not player.paused:
                player.pause()
            else:
                player.resume()
            await self._respond_with_controls(interaction, player)
        return callback

    def _make_skip_cb(self, player: GuildPlayer):
        async def callback(interaction: discord.Interaction):
            if self._is_stale(player):
                await interaction.response.send_message(self.data.get_lang(interaction.guild.id)["error"]["session_expired"], ephemeral=True)
                return
            lang = self.data.get_lang(interaction.guild.id)
            if len(player.queue) > 1 or player.autoplay:
                player.interaction_active = True
                try:
                    await interaction.response.defer()
                    if player.paused:
                        player.resume()
                    await self._play_next(player)
                finally:
                    player.interaction_active = False
            else:
                await interaction.response.send_message(lang["error"]["can_not_skip"], ephemeral=True)
        return callback

    def _make_queue_toggle_cb(self, player: GuildPlayer):
        async def callback(interaction: discord.Interaction):
            if self._is_stale(player):
                await interaction.response.send_message(self.data.get_lang(interaction.guild.id)["error"]["session_expired"], ephemeral=True)
                return
            player.show_queue = not player.show_queue
            await self._respond_with_controls(interaction, player)
        return callback

    def _make_stop_cb(self, player: GuildPlayer):
        async def callback(interaction: discord.Interaction):
            if self._is_stale(player):
                await interaction.response.send_message(self.data.get_lang(interaction.guild.id)["error"]["session_expired"], ephemeral=True)
                return
            await interaction.response.defer()
            try:
                lang = self.data.get_lang(interaction.guild.id)
                await self._stop_player(player)
                await interaction.followup.send(embed=build_stopped(lang))
            except Exception:
                log.error("Button callback error", exc_info=True)
        return callback

    def _make_loop_cb(self, player: GuildPlayer):
        async def callback(interaction: discord.Interaction):
            if self._is_stale(player):
                await interaction.response.send_message(self.data.get_lang(interaction.guild.id)["error"]["session_expired"], ephemeral=True)
                return
            player.looping = not player.looping
            await self._respond_with_controls(interaction, player)
        return callback

    def _make_shuffle_cb(self, player: GuildPlayer):
        async def callback(interaction: discord.Interaction):
            if self._is_stale(player):
                await interaction.response.send_message(self.data.get_lang(interaction.guild.id)["error"]["session_expired"], ephemeral=True)
                return
            if not player.shuffle:
                player.original_queue = list(player.queue)
                current = player.queue[0]
                rest = player.queue[1:]
                random_shuffle(rest)
                player.queue = [current] + rest
                player.shuffle = True
            else:
                if player.original_queue:
                    remaining = set(player.queue)
                    restored = [v for v in player.original_queue if v in remaining]
                    if player.current and player.current in restored:
                        restored.remove(player.current)
                        restored.insert(0, player.current)
                    player.queue = restored
                player.shuffle = False
                player.original_queue = None
            await self._respond_with_controls(interaction, player)
        return callback

    def _make_autoplay_cb(self, player: GuildPlayer):
        async def callback(interaction: discord.Interaction):
            if self._is_stale(player):
                await interaction.response.send_message(self.data.get_lang(interaction.guild.id)["error"]["session_expired"], ephemeral=True)
                return
            player.autoplay = not player.autoplay
            await self._respond_with_controls(interaction, player)
        return callback


async def setup(bot: commands.Bot) -> None:
    data = bot.data  # type: ignore[attr-defined]
    youtube = bot.youtube  # type: ignore[attr-defined]
    cog = MusicCog(bot, data, youtube)

    # Register right-click message context menu
    ctx_menu = app_commands.ContextMenu(name="Play this", callback=cog._play_from_message)
    bot.tree.add_command(ctx_menu)

    await bot.add_cog(cog)
