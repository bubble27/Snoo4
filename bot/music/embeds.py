from __future__ import annotations

import logging
from datetime import datetime
from math import ceil, floor
from typing import TYPE_CHECKING

import discord

from bot.config import BOT_COLOR, EMOJIS, ICONS, PLAYBAR_LENGTH
from bot.utils import format_time, format_time_ago, format_views

if TYPE_CHECKING:
    from bot.music.player import GuildPlayer

log = logging.getLogger(__name__)


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

    # Focus indicator
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


def build_nowplaying(player: GuildPlayer, cache: dict, lang: dict) -> list:
    """Returns [main_embed, thumbnail_embed, button_embed]."""
    video_id = player.current
    if video_id is None:
        return []

    vid = cache.get(video_id, {})
    palette = vid.get("palette", ((200, 200, 200), (150, 150, 150), (100, 100, 100)))

    # Progress
    elapsed = player.elapsed_seconds()
    duration = vid.get("secs_length", 0)
    percent = min(elapsed / duration, 1.0) if duration > 0 else 0.0
    bar = render_playbar(PLAYBAR_LENGTH, percent)
    playbar_text = f"{format_time(elapsed, mono=True)}   {bar}   {format_time(duration, mono=True)}"

    # Time ago
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

    # Views
    if player.show_queue:
        views_text = f"{vid.get('views', 0):,}"
    else:
        views_text = format_views(vid.get("views", 0), lang)

    color0 = _color_from_palette(palette, 0)
    color1 = _color_from_palette(palette, 1)
    color2 = _color_from_palette(palette, 2)

    # Thumbnail embed
    thumb_embed = discord.Embed(color=color0)
    thumb_embed.set_image(url=vid.get("thumbnail", ""))
    thumb_embed.set_author(
        name=lang["ui"]["title"]["nowplaying"].title(),
        icon_url=ICONS["music"],
    )

    # Main embed
    main_embed = discord.Embed(
        title=vid.get("title", "Unknown"),
        url=f"https://www.youtube.com/watch?v={video_id}",
        description=f'[{vid.get("channel_name", "?")}]({vid.get("channel_link", "")})\n\n{playbar_text}',
        color=color1,
    )

    # Button/info embed
    views_label = lang["ui"]["field"]["views"]
    btn_embed = discord.Embed(
        description=f"{views_text} {views_label} - {time_ago}",
        color=color2,
    )

    # Queue display in button embed
    if player.show_queue:
        _add_queue_fields(btn_embed, player, cache, lang)
    elif player.autoplay and player.recommended_vid and player.recommended_vid in cache:
        # Show autoplay recommendation even when queue is collapsed
        rec_title = cache[player.recommended_vid].get("title", "Unknown")
        btn_embed.add_field(name=lang["ui"]["field"]["autoplay"].title(), value=rec_title, inline=False)

    return [main_embed, thumb_embed, btn_embed]


def _add_queue_fields(embed: discord.Embed, player: GuildPlayer, cache: dict, lang: dict) -> None:
    songs = ""
    durations = ""
    total_time = sum(cache.get(v, {}).get("secs_length", 0) for v in player.queue)

    chr_per_row = 40
    early_break = False
    for i, video_id in enumerate(player.queue):
        if i == 0:
            continue
        vid = cache.get(video_id, {})
        title = vid.get("title", "Unknown")
        if len(songs) < 1024 - chr_per_row:
            truncated = title[:chr_per_row] + ("..." if len(title) > chr_per_row else "")
            songs += f"**{i}** {truncated}\n"
            durations += format_time(vid.get("secs_length", 0)) + "\n"
        else:
            early_break = True
            remaining = len(player.queue) - i
            embed.set_footer(text=lang["ui"]["field"]["queue_footer"]["full"].format(remaining, format_time(total_time)))
            break

    if songs:
        embed.add_field(name=lang["ui"]["field"]["next_up"].title(), value=songs, inline=True)
        embed.add_field(name=lang["ui"]["field"]["duration"].title(), value=durations, inline=True)

    if player.autoplay and player.recommended_vid and player.recommended_vid in cache:
        rec_title = cache[player.recommended_vid].get("title", "Unknown")
        embed.add_field(name=lang["ui"]["field"]["autoplay"].title(), value=rec_title, inline=False)

    if not early_break:
        embed.set_footer(text=lang["ui"]["field"]["queue_footer"]["short"].format(format_time(total_time)))


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
    embed.set_author(
        name=lang["ui"]["title"]["queued"].title(),
        icon_url=ICONS["music"],
    )
    return embed


def build_stopped(lang: dict) -> discord.Embed:
    return discord.Embed(
        title=lang["ui"]["field"]["stopped"].title(),
        description="",
        color=BOT_COLOR,
    ).set_author(
        name=lang["ui"]["title"]["stopped"].title(),
        icon_url=ICONS["music"],
    )


def build_queue_ended(lang: dict) -> discord.Embed:
    return discord.Embed(
        title=lang["ui"]["field"].get("queue_end", "play something new!").title(),
        description="",
        color=BOT_COLOR,
    ).set_author(
        name=lang["ui"]["title"]["queue_end"].title(),
        icon_url=ICONS["music"],
    )
