from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

import discord
from discord.ui import ActionRow, Button

from bot.config import EMOJIS

if TYPE_CHECKING:
    from bot.music.player import GuildPlayer

log = logging.getLogger(__name__)


def build_nowplaying_buttons(
    player: GuildPlayer,
    liked: bool,
    disliked: bool,
    *,
    on_like: Callable,
    on_back: Callable,
    on_pause: Callable,
    on_skip: Callable,
    on_toggle_queue: Callable,
    on_dislike: Callable,
    on_loop: Callable,
    on_shuffle: Callable,
    on_autoplay: Callable,
    on_stop: Callable,
) -> list[ActionRow]:
    """Build button ActionRows for the now-playing container."""

    # Row 0: like, back, pause, skip, expand/collapse
    like_emoji = EMOJIS["like_on"] if liked else EMOJIS["like_off"]
    like_btn = Button(emoji=like_emoji)
    like_btn.callback = on_like

    back_btn = Button(emoji=EMOJIS["back"])
    back_btn.callback = on_back

    pause_emoji = EMOJIS["play"] if player.paused else EMOJIS["pause"]
    pause_btn = Button(emoji=pause_emoji)
    pause_btn.callback = on_pause

    skip_btn = Button(emoji=EMOJIS["skip"])
    skip_btn.callback = on_skip

    queue_emoji = EMOJIS["collapse"] if player.show_queue else EMOJIS["extend"]
    queue_btn = Button(emoji=queue_emoji)
    queue_btn.callback = on_toggle_queue

    rows = [ActionRow(like_btn, back_btn, pause_btn, skip_btn, queue_btn)]

    # Row 1 (only when expanded): dislike, loop, shuffle, autoplay, stop
    if player.show_queue:
        dislike_emoji = EMOJIS["dislike_on"] if disliked else EMOJIS["dislike_off"]
        dislike_btn = Button(emoji=dislike_emoji)
        dislike_btn.callback = on_dislike

        loop_emoji = EMOJIS["loop_on"] if player.looping else EMOJIS["loop_off"]
        loop_btn = Button(emoji=loop_emoji)
        loop_btn.callback = on_loop

        shuffle_emoji = EMOJIS["shuffle_on"] if player.shuffle else EMOJIS["shuffle_off"]
        shuffle_btn = Button(emoji=shuffle_emoji)
        shuffle_btn.callback = on_shuffle

        autoplay_emoji = EMOJIS["autoplay_on"] if player.autoplay else EMOJIS["autoplay_off"]
        autoplay_btn = Button(emoji=autoplay_emoji)
        autoplay_btn.callback = on_autoplay

        stop_btn = Button(emoji=EMOJIS["delete"])
        stop_btn.callback = on_stop

        rows.append(ActionRow(dislike_btn, loop_btn, shuffle_btn, autoplay_btn, stop_btn))

    return rows
