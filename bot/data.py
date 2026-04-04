from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
from typing import TYPE_CHECKING

import discord

from bot.config import DATA_CHANNELS, DEFAULT_SETTINGS

if TYPE_CHECKING:
    from discord.ext.commands import Bot

log = logging.getLogger(__name__)


class DataManager:
    """Handles all persistent data: loading from Discord channels, saving, and verification."""

    def __init__(self) -> None:
        self.profile_data: dict[int, dict[int, dict]] = defaultdict(dict)
        self.channel_messages: dict[int, dict[int, list[int]]] = defaultdict(dict)
        self.song_history: dict[int, dict[int, list[dict]]] = defaultdict(dict)
        self.server_config: dict[int, dict] = defaultdict(dict)
        self.playlists: dict[int, dict] = defaultdict(dict)
        self.language: dict[str, dict] = {}
        self.missing_translations: dict[str, dict] = defaultdict(dict)

    async def initialize(self, bot: Bot) -> None:
        self._load_language()
        await self._load_all(bot)
        self._verify_translations()

    def _load_language(self) -> None:
        with open("System/language.json", encoding="utf-8") as f:
            self.language = json.load(f)

    async def _load_all(self, bot: Bot) -> None:
        raw_profile = await self._download(bot, DATA_CHANNELS["profile"], "profile")
        raw_messages = await self._download(bot, DATA_CHANNELS["channel_messages"], "channel_messages")
        raw_history = await self._download(bot, DATA_CHANNELS["song_history"], "song_history")
        raw_config = await self._download(bot, DATA_CHANNELS["server_config"], "server_config")
        raw_playlists = await self._download(bot, DATA_CHANNELS["playlists"], "playlists")

        self._keys_to_int(raw_profile, self.profile_data, nested=True)
        self._keys_to_int(raw_messages, self.channel_messages, nested=True)
        self._keys_to_int(raw_history, self.song_history, nested=True)
        self._keys_to_int(raw_config, self.server_config, nested=False)
        self._keys_to_int(raw_playlists, self.playlists, nested=False)

    @staticmethod
    async def _download(bot: Bot, channel_id: int, name: str) -> dict:
        os.makedirs("Data Files", exist_ok=True)
        channel = bot.get_channel(channel_id)
        if channel is None:
            log.warning("Data channel %s (%d) not found", name, channel_id)
            return {}
        async for message in channel.history(limit=1):
            if message.attachments:
                path = f"Data Files/{name}.json"
                await message.attachments[0].save(path)
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
        return {}

    @staticmethod
    def _keys_to_int(raw: dict, target: dict, nested: bool) -> None:
        if nested:
            for key, inner in raw.items():
                for inner_key, value in inner.items():
                    target[int(key)][int(inner_key)] = value
        else:
            for key, value in raw.items():
                target[int(key)] = value

    async def save_all(self, bot: Bot) -> None:
        start = datetime.now()

        await self._upload(bot, DATA_CHANNELS["profile"], "profile", self.profile_data)
        await self._upload(bot, DATA_CHANNELS["channel_messages"], "channel_messages", self.channel_messages)
        await self._upload(bot, DATA_CHANNELS["server_config"], "server_config", self.server_config)
        await self._upload(bot, DATA_CHANNELS["song_history"], "song_history", self.song_history)
        await self._upload(bot, DATA_CHANNELS["playlists"], "playlists", self.playlists)

        elapsed_ms = round((datetime.now() - start).total_seconds() * 1000)
        log.info("Saved all data in %dms", elapsed_ms)

    @staticmethod
    async def _upload(bot: Bot, channel_id: int, name: str, data: dict) -> None:
        channel = bot.get_channel(channel_id)
        if channel is None:
            log.warning("Upload channel %s (%d) not found", name, channel_id)
            return
        os.makedirs("Data Files", exist_ok=True)
        path = f"Data Files/{name}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        await channel.send(file=discord.File(path))

    # --- Data verification / defaults ---

    def verify_settings(self, guild_id: int) -> dict:
        if guild_id not in self.server_config:
            self.server_config[guild_id] = deepcopy(DEFAULT_SETTINGS)
        return self.server_config[guild_id]

    def verify_profile(self, guild_id: int, user_id: int) -> dict:
        if guild_id not in self.profile_data:
            self.profile_data[guild_id] = {}
        if user_id not in self.profile_data[guild_id]:
            self.profile_data[guild_id][user_id] = {
                "messages": [0],
                "vc_time": [0],
                "friendship": [0],
                "karma": [0],
            }
        return self.profile_data[guild_id][user_id]

    def get_lang(self, guild_id: int) -> dict:
        settings = self.verify_settings(guild_id)
        lang_key = settings.get("lang_set", "English")
        return self.language.get(lang_key, self.language["English"])

    def add_daily_entry(self) -> None:
        for guild in self.profile_data.values():
            for user_data in guild.values():
                for stat_list in user_data.values():
                    stat_list.append(0)
        for channels in self.channel_messages.values():
            for msg_list in channels.values():
                msg_list.append(0)
        for guild_history in self.song_history.values():
            for user_history in guild_history.values():
                user_history.append({})

    def update_vc_time(self, users_in_vc: dict[int, list[int]]) -> None:
        for guild_id, user_ids in users_in_vc.items():
            for user_id in user_ids:
                profile = self.verify_profile(guild_id, user_id)
                profile["vc_time"][-1] = round(profile["vc_time"][-1] + 0.1, 1)

    # --- Translation verification ---

    def _verify_translations(self) -> None:
        self._check_lang_keys("settings_info")
        self._check_lang_keys("error")
        self._check_lang_keys("notifs")
        self._check_lang_keys("ui", "title")
        self._check_lang_keys("ui", "field")

    def _check_lang_keys(self, *path: str) -> None:
        def _get_nested(d: dict, keys: tuple[str, ...]) -> dict:
            for k in keys:
                d = d[k]
            return d

        english = _get_nested(self.language["English"], path)
        for lang_name, lang_data in self.language.items():
            if lang_name == "English":
                continue
            target = _get_nested(lang_data, path)
            for key, value in english.items():
                if key not in target:
                    target[key] = value
                    # Track missing translation
                    missing = self.missing_translations
                    for p in path:
                        missing = missing.setdefault(lang_name, {}).setdefault(p, {}) if isinstance(missing, dict) else missing
                    log.debug("Missing translation: %s -> %s -> %s", lang_name, "/".join(path), key)
