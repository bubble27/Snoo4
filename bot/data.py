from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, date
from typing import TYPE_CHECKING

import discord

from bot.config import DATA_CHANNELS, DEFAULT_SETTINGS

if TYPE_CHECKING:
    from discord.ext.commands import Bot

log = logging.getLogger(__name__)

DB_DIR = "Data Files"
DB_PROFILES = os.path.join(DB_DIR, "profiles.db")
DB_CHANNELS = os.path.join(DB_DIR, "channels.db")
DB_HISTORY = os.path.join(DB_DIR, "history.db")


def _open_db(path: str) -> sqlite3.Connection:
    db = sqlite3.connect(path, isolation_level=None)  # autocommit mode
    db.execute("PRAGMA journal_mode=WAL")
    return db


class DataManager:
    """Handles all persistent data via SQLite locally, synced to Discord channels as backup."""

    def __init__(self) -> None:
        # Small config data (kept in memory, synced as JSON to Discord)
        self.server_config: dict[int, dict] = defaultdict(dict)
        self.playlists: dict[int, dict] = defaultdict(dict)
        self.language: dict[str, dict] = {}
        self.missing_translations: dict[str, dict] = defaultdict(dict)

        # SQLite connections
        self.db_profiles: sqlite3.Connection | None = None
        self.db_channels: sqlite3.Connection | None = None
        self.db_history: sqlite3.Connection | None = None

    def _init_dbs(self) -> None:
        os.makedirs(DB_DIR, exist_ok=True)

        self.db_profiles = _open_db(DB_PROFILES)
        self.db_profiles.executescript("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                guild_id   INTEGER NOT NULL,
                user_id    INTEGER NOT NULL,
                date       TEXT    NOT NULL,
                messages   INTEGER DEFAULT 0,
                karma      INTEGER DEFAULT 0,
                friendship INTEGER DEFAULT 0,
                vc_time    REAL    DEFAULT 0.0,
                PRIMARY KEY (guild_id, user_id, date)
            );
            CREATE INDEX IF NOT EXISTS idx_daily_guild_user
                ON daily_stats (guild_id, user_id);
        """)


        self.db_channels = _open_db(DB_CHANNELS)
        self.db_channels.executescript("""
            CREATE TABLE IF NOT EXISTS channel_stats (
                guild_id   INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                date       TEXT    NOT NULL,
                messages   INTEGER DEFAULT 0,
                PRIMARY KEY (guild_id, channel_id, date)
            );
        """)


        self.db_history = _open_db(DB_HISTORY)
        self.db_history.executescript("""
            CREATE TABLE IF NOT EXISTS song_history (
                guild_id    INTEGER NOT NULL,
                user_id     INTEGER NOT NULL,
                video_id    TEXT    NOT NULL,
                date        TEXT    NOT NULL,
                retention   REAL    DEFAULT 0.0,
                listen_time INTEGER DEFAULT 0,
                timestamp   TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_history_guild_user
                ON song_history (guild_id, user_id);
        """)


    async def initialize(self, bot: Bot) -> None:
        self._load_language()
        self._init_dbs()
        await self._load_config(bot)
        await self._migrate_legacy(bot)
        self._verify_translations()

    def _load_language(self) -> None:
        with open("System/language.json", encoding="utf-8") as f:
            self.language = json.load(f)

    async def _load_config(self, bot: Bot) -> None:
        raw_config = await self._download_json(bot, DATA_CHANNELS["server_config"], "server_config")
        raw_playlists = await self._download_json(bot, DATA_CHANNELS["playlists"], "playlists")
        self._keys_to_int(raw_config, self.server_config, nested=False)
        self._keys_to_int(raw_playlists, self.playlists, nested=False)

    async def _migrate_legacy(self, bot: Bot) -> None:
        """One-time migration: import old array-based JSON data into SQLite."""
        row = self.db_profiles.execute("SELECT COUNT(*) FROM daily_stats").fetchone()
        if row[0] > 0:
            return

        log.info("Checking for legacy data to migrate...")
        today = date.today().isoformat()
        now = datetime.now().isoformat()

        # Migrate profile data
        raw = await self._download_json(bot, DATA_CHANNELS["profile"], "profile")
        if raw:
            count = 0
            for guild_str, users in raw.items():
                guild_id = int(guild_str)
                for user_str, stats in users.items():
                    user_id = int(user_str)
                    messages = sum(stats.get("messages", [0]))
                    karma = sum(stats.get("karma", [0]))
                    friendship = sum(stats.get("friendship", [0]))
                    vc_time = round(sum(stats.get("vc_time", [0])), 2)
                    if messages or karma or friendship or vc_time:
                        self.db_profiles.execute(
                            "INSERT OR REPLACE INTO daily_stats VALUES (?,?,?,?,?,?,?)",
                            (guild_id, user_id, today, messages, karma, friendship, vc_time),
                        )
                        count += 1
    
            if count:
                log.info("Migrated %d profile records", count)

        # Migrate channel messages
        raw = await self._download_json(bot, DATA_CHANNELS["channel_messages"], "channel_messages")
        if raw:
            count = 0
            for guild_str, channels in raw.items():
                guild_id = int(guild_str)
                for chan_str, msg_list in channels.items():
                    channel_id = int(chan_str)
                    total = sum(msg_list) if isinstance(msg_list, list) else 0
                    if total:
                        self.db_channels.execute(
                            "INSERT OR REPLACE INTO channel_stats VALUES (?,?,?,?)",
                            (guild_id, channel_id, today, total),
                        )
                        count += 1
    
            if count:
                log.info("Migrated %d channel message records", count)

        # Migrate song history
        raw = await self._download_json(bot, DATA_CHANNELS["song_history"], "song_history")
        if raw:
            count = 0
            for guild_str, users in raw.items():
                guild_id = int(guild_str)
                for user_str, days in users.items():
                    user_id = int(user_str)
                    if not isinstance(days, list):
                        continue
                    for day_data in days:
                        if not isinstance(day_data, dict):
                            continue
                        for video_id, watches in day_data.items():
                            for watch in watches:
                                self.db_history.execute(
                                    "INSERT INTO song_history VALUES (?,?,?,?,?,?,?)",
                                    (guild_id, user_id, video_id, today,
                                     watch.get("retention", 0), watch.get("listen_time", 0), now),
                                )
                                count += 1
    
            if count:
                log.info("Migrated %d song history records", count)

    # --- Discord channel I/O ---

    @staticmethod
    async def _download_json(bot: Bot, channel_id: int, name: str) -> dict:
        os.makedirs(DB_DIR, exist_ok=True)
        channel = bot.get_channel(channel_id)
        if channel is None:
            log.warning("Data channel %s (%d) not found", name, channel_id)
            return {}
        async for message in channel.history(limit=1):
            if message.attachments:
                path = os.path.join(DB_DIR, f"{name}.json")
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

    @staticmethod
    async def _upload_json(bot: Bot, channel_id: int, name: str, data) -> None:
        channel = bot.get_channel(channel_id)
        if channel is None:
            return
        path = os.path.join(DB_DIR, f"{name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        await channel.send(file=discord.File(path))

    @staticmethod
    async def _upload_file(bot: Bot, channel_id: int, path: str, filename: str) -> None:
        channel = bot.get_channel(channel_id)
        if channel is None:
            return
        if os.path.exists(path):
            await channel.send(file=discord.File(path, filename=filename))

    async def save_all(self, bot: Bot) -> None:
        start = datetime.now()

        # Upload JSON config to Discord
        await self._upload_json(bot, DATA_CHANNELS["server_config"], "server_config", self.server_config)
        await self._upload_json(bot, DATA_CHANNELS["playlists"], "playlists", self.playlists)

        # Upload each database to its respective Discord channel
        await self._upload_file(bot, DATA_CHANNELS["profile"], DB_PROFILES, "profiles.db")
        await self._upload_file(bot, DATA_CHANNELS["channel_messages"], DB_CHANNELS, "channels.db")
        await self._upload_file(bot, DATA_CHANNELS["song_history"], DB_HISTORY, "history.db")

        elapsed_ms = round((datetime.now() - start).total_seconds() * 1000)
        log.info("Saved all data in %dms", elapsed_ms)

    # --- Daily stats (profiles.db) ---

    def _today(self) -> str:
        return date.today().isoformat()

    def _ensure_today(self, guild_id: int, user_id: int) -> None:
        self.db_profiles.execute(
            "INSERT OR IGNORE INTO daily_stats (guild_id, user_id, date) VALUES (?,?,?)",
            (guild_id, user_id, self._today()),
        )

    def add_messages(self, guild_id: int, user_id: int, count: int = 1) -> None:
        self._ensure_today(guild_id, user_id)
        self.db_profiles.execute(
            "UPDATE daily_stats SET messages = messages + ? WHERE guild_id=? AND user_id=? AND date=?",
            (count, guild_id, user_id, self._today()),
        )

    def add_karma(self, guild_id: int, user_id: int, delta: int) -> None:
        self._ensure_today(guild_id, user_id)
        self.db_profiles.execute(
            "UPDATE daily_stats SET karma = karma + ? WHERE guild_id=? AND user_id=? AND date=?",
            (delta, guild_id, user_id, self._today()),
        )

    def add_friendship(self, guild_id: int, user_id: int, delta: int) -> None:
        self._ensure_today(guild_id, user_id)
        self.db_profiles.execute(
            "UPDATE daily_stats SET friendship = friendship + ? WHERE guild_id=? AND user_id=? AND date=?",
            (delta, guild_id, user_id, self._today()),
        )

    def add_vc_time(self, guild_id: int, user_id: int, hours: float) -> None:
        self._ensure_today(guild_id, user_id)
        self.db_profiles.execute(
            "UPDATE daily_stats SET vc_time = vc_time + ? WHERE guild_id=? AND user_id=? AND date=?",
            (round(hours, 4), guild_id, user_id, self._today()),
        )

    def get_profile_totals(self, guild_id: int, user_id: int) -> dict:
        row = self.db_profiles.execute(
            "SELECT COALESCE(SUM(messages),0), COALESCE(SUM(karma),0), "
            "COALESCE(SUM(friendship),0), COALESCE(SUM(vc_time),0.0) "
            "FROM daily_stats WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        ).fetchone()
        return {
            "messages": row[0],
            "karma": row[1],
            "friendship": row[2],
            "vc_time": round(row[3], 1),
        }

    def get_stat_series(self, guild_id: int, user_id: int, stat: str) -> list[tuple[str, float]]:
        if stat not in ("messages", "karma", "friendship", "vc_time"):
            return []
        rows = self.db_profiles.execute(
            f"SELECT date, {stat} FROM daily_stats WHERE guild_id=? AND user_id=? ORDER BY date",
            (guild_id, user_id),
        ).fetchall()
        return rows

    # --- Channel stats (channels.db) ---

    def add_channel_message(self, guild_id: int, channel_id: int) -> None:
        today = self._today()
        self.db_channels.execute(
            "INSERT INTO channel_stats (guild_id, channel_id, date, messages) VALUES (?,?,?,1) "
            "ON CONFLICT(guild_id, channel_id, date) DO UPDATE SET messages = messages + 1",
            (guild_id, channel_id, today),
        )

    # --- Song history (history.db) ---

    def record_listen(
        self, guild_id: int, user_id: int, video_id: str,
        retention: float, listen_time: int,
    ) -> None:
        self.db_history.execute(
            "INSERT INTO song_history VALUES (?,?,?,?,?,?,?)",
            (guild_id, user_id, video_id, self._today(), retention, listen_time,
             datetime.now().isoformat()),
        )

    def get_top_songs(self, guild_id: int, user_id: int, limit: int = 10) -> list[tuple[str, float]]:
        rows = self.db_history.execute(
            "SELECT video_id, ROUND(SUM(retention), 2) as total "
            "FROM song_history WHERE guild_id=? AND user_id=? "
            "GROUP BY video_id ORDER BY total DESC LIMIT ?",
            (guild_id, user_id, limit),
        ).fetchall()
        return rows

    # --- Cleanup ---

    def close(self) -> None:
        for db in (self.db_profiles, self.db_channels, self.db_history):
            if db:
                try:
                    db.close()
                except Exception:
                    pass
        self.db_profiles = self.db_channels = self.db_history = None
        log.info("Database connections closed")

    # --- Config (in-memory + Discord JSON) ---

    def verify_settings(self, guild_id: int) -> dict:
        if guild_id not in self.server_config:
            self.server_config[guild_id] = deepcopy(DEFAULT_SETTINGS)
        return self.server_config[guild_id]

    def get_lang(self, guild_id: int) -> dict:
        settings = self.verify_settings(guild_id)
        lang_key = settings.get("lang_set", "English")
        return self.language.get(lang_key, self.language["English"])

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
                    log.debug("Missing translation: %s -> %s -> %s", lang_name, "/".join(path), key)
