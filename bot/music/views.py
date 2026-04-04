from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable, Coroutine

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
        *,
        on_like: Callable,
        on_back: Callable,
        on_pause: Callable,
        on_skip: Callable,
        on_toggle_queue: Callable,
        on_stop: Callable,
    ) -> None:
        super().__init__(timeout=None)

        # Like button
        like_emoji = EMOJIS["like"] if liked else EMOJIS["not_like"]
        btn = Button(emoji=like_emoji)
        btn.callback = on_like
        self.add_item(btn)

        # Back button
        btn = Button(emoji=EMOJIS["back"])
        btn.callback = on_back
        self.add_item(btn)

        # Pause/Play button
        pause_emoji = EMOJIS["play"] if player.paused else EMOJIS["pause"]
        btn = Button(emoji=pause_emoji)
        btn.callback = on_pause
        self.add_item(btn)

        # Skip button
        btn = Button(emoji=EMOJIS["skip"])
        btn.callback = on_skip
        self.add_item(btn)

        # Expand/Collapse queue button
        queue_emoji = EMOJIS["collapse"] if player.show_queue else EMOJIS["extend"]
        btn = Button(emoji=queue_emoji)
        btn.callback = on_toggle_queue
        self.add_item(btn)

        # Stop button (only when queue is shown)
        if player.show_queue:
            btn = Button(emoji=EMOJIS["delete"])
            btn.callback = on_stop
            self.add_item(btn)
