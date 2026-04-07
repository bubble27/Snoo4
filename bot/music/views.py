from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

import discord
from discord.ui import Button, View

from bot.config import EMOJIS

if TYPE_CHECKING:
    from bot.music.player import GuildPlayer

log = logging.getLogger(__name__)


class NowPlayingView(View):
    """Interactive button controls for the now-playing display."""

    def __init__(
        self,
        player: GuildPlayer,
        liked: bool,
        lang: dict,
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
    ) -> None:
        super().__init__(timeout=None)

        # Row 0: like, back, pause, skip, expand/collapse
        like_emoji = EMOJIS["like"] if liked else EMOJIS["not_like"]
        btn = Button(emoji=like_emoji, row=0)
        btn.callback = on_like
        self.add_item(btn)

        btn = Button(emoji=EMOJIS["back"], row=0)
        btn.callback = on_back
        self.add_item(btn)

        pause_emoji = EMOJIS["play"] if player.paused else EMOJIS["pause"]
        btn = Button(emoji=pause_emoji, row=0)
        btn.callback = on_pause
        self.add_item(btn)

        btn = Button(emoji=EMOJIS["skip"], row=0)
        btn.callback = on_skip
        self.add_item(btn)

        queue_emoji = EMOJIS["collapse"] if player.show_queue else EMOJIS["extend"]
        btn = Button(emoji=queue_emoji, row=0)
        btn.callback = on_toggle_queue
        self.add_item(btn)

        # Row 1 (only when expanded): dislike, loop, shuffle, autoplay, stop
        if player.show_queue:
            btn = Button(emoji=EMOJIS["downvote"], row=1)
            btn.callback = on_dislike
            self.add_item(btn)

            loop_style = discord.ButtonStyle.primary if player.looping else discord.ButtonStyle.secondary
            btn = Button(emoji="\U0001f501", style=loop_style, row=1)
            btn.callback = on_loop
            self.add_item(btn)

            shuffle_style = discord.ButtonStyle.primary if player.shuffle else discord.ButtonStyle.secondary
            btn = Button(emoji="\U0001f500", style=shuffle_style, row=1)
            btn.callback = on_shuffle
            self.add_item(btn)

            autoplay_style = discord.ButtonStyle.primary if player.autoplay else discord.ButtonStyle.secondary
            btn = Button(emoji="\u25b6\ufe0f", style=autoplay_style, row=1)
            btn.callback = on_autoplay
            self.add_item(btn)

            btn = Button(emoji=EMOJIS["delete"], row=1)
            btn.callback = on_stop
            self.add_item(btn)
