from __future__ import annotations

import logging
from datetime import datetime
from math import ceil, floor
from typing import TYPE_CHECKING

import discord
from discord.ui import ActionRow, Container, LayoutView, MediaGallery, Separator, TextDisplay

from bot.config import BOT_COLOR, EMOJIS, ICONS, PLAYBAR_LENGTH
from bot.utils import format_time, format_time_ago, format_views, truncate

if TYPE_CHECKING:
    from bot.music.player import GuildPlayer

log = logging.getLogger(__name__)

NP_PLAYBAR_LENGTH = 20


def render_playbar(length: int, percent: float) -> str:
    pb = EMOJIS["playbar"]
    fills = pb["fills_and_caps"]
    focus = pb["focus_bars"]

    segments = percent * length
    snap_points = len(focus["body"])
    focus_index = floor((segments % 1) * 0.999 * snap_points)

    playbar = ""
    taken_fills = 1
    taken_empty = 2

    if segments > 1 + 1 / snap_points:
        playbar += fills[0]
        taken_fills += 1

    if 1 - 1 / snap_points < segments < 1 + 1 / snap_points:
        if segments < 1:
            taken_empty += 1
        else:
            taken_fills += 1

    if 1 - 1 / snap_points < length - segments < 1 + 1 / snap_points and length - segments < 1:
        taken_fills += 1

    if segments > 1 and length - segments > 1:
        if focus_index == 0:
            taken_fills += 1
        if focus_index == snap_points - 1:
            taken_empty += 1

    playbar += fills[2] * ceil(segments - taken_fills)

    if segments < 1:
        playbar += focus["cap_r"][focus_index]
    elif segments < 1 + 1 / snap_points:
        playbar += focus["cap_r"][-1]
    elif percent >= 1:
        playbar += focus["cap_l"][-1]
    elif length - segments < 1:
        playbar += focus["cap_l"][focus_index]
    elif length - segments < 1 + 1 / snap_points:
        playbar += focus["cap_l"][0]
    else:
        playbar += focus["body"][focus_index]

    playbar += fills[3] * ceil(length - segments - taken_empty)

    if length - segments > 1 + 1 / snap_points:
        playbar += fills[1]

    return playbar


def _color_from_palette(palette: tuple, index: int) -> discord.Color:
    c = palette[index]
    return discord.Color.from_rgb(c[0], c[1], c[2])


def build_info_view(player: GuildPlayer, cache: dict, lang: dict) -> LayoutView | None:
    """Info container: thumbnail, title, channel, playbar, views. Updated every second."""
    video_id = player.current
    if video_id is None:
        return None

    vid = cache.get(video_id, {})
    palette = vid.get("palette", ((200, 200, 200), (150, 150, 150), (100, 100, 100)))

    elapsed = player.elapsed_seconds()
    duration = vid.get("secs_length", 0)
    percent = min(elapsed / duration, 1.0) if duration > 0 else 0.0
    bar = render_playbar(NP_PLAYBAR_LENGTH, percent)
    playbar_text = f"{format_time(elapsed, mono=True)}   {bar}   {format_time(duration, mono=True)}"

    date_str = str(vid.get("publish_date", ""))
    if len(date_str) >= 8:
        try:
            pub_date = datetime.strptime(date_str[:8], "%Y%m%d")
            days_delta = (datetime.now() - pub_date).total_seconds() / 86400
            time_ago = format_time_ago(days_delta, lang)
        except ValueError:
            time_ago = date_str
    else:
        time_ago = date_str

    views_text = format_views(vid.get("views", 0), lang)
    color = _color_from_palette(palette, 0)

    video_url = f"https://www.youtube.com/watch?v={video_id}"
    title = vid.get("title", "Unknown")
    channel_name = vid.get("channel_name", "?")
    channel_link = vid.get("channel_link", "")
    thumbnail = vid.get("thumbnail", "")
    views_label = lang["ui"]["field"]["views"]

    children = []
    if thumbnail:
        children.append(MediaGallery(discord.MediaGalleryItem(media=thumbnail)))
    children.append(TextDisplay(
        f"## [{title}]({video_url})\n"
        f"-# [{channel_name}]({channel_link})"
    ))
    children.append(TextDisplay(playbar_text))
    children.append(TextDisplay(f"-# {views_text} {views_label} · {time_ago}"))

    view = LayoutView(timeout=None)
    view.add_item(Container(*children, accent_colour=color))
    return view


def build_controls_view(player: GuildPlayer, cache: dict, lang: dict, buttons: list) -> LayoutView | None:
    """Controls container: buttons + queue/autoplay. Only updated on user action or song change."""
    video_id = player.current
    if video_id is None:
        return None

    vid = cache.get(video_id, {})
    palette = vid.get("palette", ((200, 200, 200), (150, 150, 150), (100, 100, 100)))
    color = _color_from_palette(palette, 1)

    children = list(buttons)

    if player.show_queue:
        queue_text = _build_queue_text(player, cache, lang)
        if queue_text:
            children.append(Separator())
            children.append(TextDisplay(queue_text))

    view = LayoutView(timeout=None)
    view.add_item(Container(*children, accent_colour=color))
    return view


def _build_queue_text(player: GuildPlayer, cache: dict, lang: dict) -> str:
    lines = []
    total_time = sum(cache.get(v, {}).get("secs_length", 0) for v in player.queue)
    early_break = False
    queue_songs = []

    for i, video_id in enumerate(player.queue):
        if i == 0:
            continue
        vid = cache.get(video_id, {})
        title = truncate(vid.get("title", "Unknown"))
        dur = format_time(vid.get("secs_length", 0))
        queue_songs.append(f"**{i}.** {title} `{dur}`")
        if len(queue_songs) >= 15:
            early_break = True
            break

    if queue_songs:
        lines.append(f"**{lang['ui']['field']['next_up'].title()}**")
        lines.extend(queue_songs)

    if player.autoplay and player.recommended_vid and player.recommended_vid in cache:
        rec_title = cache[player.recommended_vid].get("title", "Unknown")
        lines.append(f"\n**{lang['ui']['field']['autoplay'].title()}**\n{rec_title}")

    if early_break:
        remaining = len(player.queue) - 1 - len(queue_songs)
        if remaining > 0:
            lines.append(f"-# {lang['ui']['field']['queue_footer']['full'].format(remaining, format_time(total_time))}")
        else:
            lines.append(f"-# {lang['ui']['field']['queue_footer']['short'].format(format_time(total_time))}")
    elif queue_songs:
        lines.append(f"-# {lang['ui']['field']['queue_footer']['short'].format(format_time(total_time))}")

    return "\n".join(lines)


def build_queued_small(video_id: str, cache: dict, lang: dict) -> discord.Embed:
    vid = cache.get(video_id, {})
    palette = vid.get("palette", ((200, 200, 200),))
    color = _color_from_palette(palette, 0)
    embed = discord.Embed(
        title=vid.get("title", "Unknown"),
        url=f"https://www.youtube.com/watch?v={video_id}",
        description=f'[{vid.get("channel_name", "?")}]({vid.get("channel_link", "")})',
        color=color,
    )
    embed.set_thumbnail(url=vid.get("thumbnail", ""))
    embed.set_author(name=lang["ui"]["title"]["queued"].title(), icon_url=ICONS["music"])
    return embed


def build_stopped(lang: dict) -> discord.Embed:
    return discord.Embed(
        title=lang["ui"]["field"]["stopped"].title(), description="", color=BOT_COLOR,
    ).set_author(name=lang["ui"]["title"]["stopped"].title(), icon_url=ICONS["music"])


def build_queue_ended(lang: dict) -> discord.Embed:
    return discord.Embed(
        title=lang["ui"]["field"].get("queue_end", "play something new!").title(),
        description="", color=BOT_COLOR,
    ).set_author(name=lang["ui"]["title"]["queue_end"].title(), icon_url=ICONS["music"])
