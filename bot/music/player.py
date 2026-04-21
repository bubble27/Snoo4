from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

import discord

from bot.config import FFMPEG_OPTIONS

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


@dataclass
class GuildPlayer:
    """Per-guild music playback state."""

    guild_id: int
    channel: discord.TextChannel | None = None
    voice: discord.VoiceClient | None = None

    queue: list[str] = field(default_factory=list)
    past_queue: list[str] = field(default_factory=list)

    paused: bool = False
    looping: bool = False
    autoplay: bool = True
    shuffle: bool = False
    show_queue: bool = False

    start_time: datetime = field(default_factory=datetime.now)
    pause_time: datetime | None = None

    # Display: info container (updates every second) + controls container (buttons, only on action)
    display_msg: discord.Message | None = None
    controls_msg: discord.Message | None = None

    # Update loop
    update_task: asyncio.Task | None = None
    update_count: int = 0

    processing: bool = False
    refetch_tried: bool = False
    recommended_vid: str | None = None
    original_queue: list[str] | None = None

    @property
    def current(self) -> str | None:
        return self.queue[0] if self.queue else None

    @property
    def is_playing(self) -> bool:
        return self.voice is not None and self.voice.is_playing()

    @property
    def is_connected(self) -> bool:
        return self.voice is not None and self.voice.is_connected()

    async def connect(self, voice_channel: discord.VoiceChannel) -> None:
        if self.is_connected:
            await self.voice.move_to(voice_channel)
        else:
            self.voice = await voice_channel.connect()

    async def disconnect(self) -> None:
        if self.voice and self.voice.is_connected():
            self.voice.stop()
            await self.voice.disconnect()
        self.voice = None

    async def play(self, audio_url: str) -> bool:
        if not self.is_connected:
            if not await self._try_reconnect():
                return False

        if self.voice.is_playing():
            self.voice.stop()

        self.start_time = datetime.now()
        try:
            source = discord.FFmpegPCMAudio(
                source=audio_url,
                stderr=sys.stderr,
                **FFMPEG_OPTIONS,
            )

            def _after(error: Exception | None) -> None:
                if error:
                    log.error("FFmpeg playback error: %s", error)

            self.voice.play(source, after=_after)
            self.refetch_tried = False
            return True
        except Exception as e:
            log.error("FFmpeg playback error: %s", e, exc_info=True)
            return False

    async def _try_reconnect(self) -> bool:
        if self.voice is None:
            return False
        channel = self.voice.channel
        if channel is None:
            log.warning("Cannot reconnect: no channel reference")
            return False
        try:
            try:
                await self.voice.disconnect(force=True)
            except Exception:
                pass
            self.voice = await channel.connect()
            log.info("Reconnected to voice in guild %d", self.guild_id)
            return True
        except Exception as e:
            log.error("Reconnection failed: %s", e)
            return False

    def pause(self) -> None:
        if self.voice and self.voice.is_playing():
            self.voice.pause()
            self.paused = True
            self.pause_time = datetime.now()

    def resume(self) -> None:
        if self.voice and self.voice.is_paused():
            self.voice.resume()
            self.paused = False
            if self.pause_time:
                self.start_time += datetime.now() - self.pause_time
                self.pause_time = None

    def elapsed_seconds(self) -> float:
        return (datetime.now() - self.start_time).total_seconds()

    def cancel_update_task(self) -> None:
        if self.update_task and not self.update_task.done():
            self.update_task.cancel()
            self.update_task = None
