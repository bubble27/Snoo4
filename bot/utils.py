from __future__ import annotations

import re
from contextlib import suppress
from math import floor
from urllib.parse import parse_qs, urlparse

_URL_RE = re.compile(
    r"(?i)\b((?:https?://|www\d{0,3}[.]|[a-z0-9.\-]+[.][a-z]{2,4}/)"
    r"(?:[^\s()<>]+|\(([^\s()<>]+|(\([^\s()<>]+\)))*\))+"
    r"(?:\(([^\s()<>]+|(\([^\s()<>]+\)))*\)|[^\s`!()\[\]{};:'\".,<>?\u00ab\u00bb\u201c\u201d\u2018\u2019]))"
)


# Unicode monospace digits (U+1D7F6-U+1D7FF) — all render at equal width in Discord
_MONO_DIGITS = str.maketrans("0123456789", "\U0001D7F6\U0001D7F7\U0001D7F8\U0001D7F9\U0001D7FA\U0001D7FB\U0001D7FC\U0001D7FD\U0001D7FE\U0001D7FF")


def format_time(secs: int | float, mono: bool = False) -> str:
    secs = int(secs)
    hours, remainder = divmod(secs, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours >= 1:
        result = f"{hours}:{minutes:02d}:{seconds:02d}"
    else:
        result = f"{minutes}:{seconds:02d}"
    if mono:
        return result.translate(_MONO_DIGITS)
    return result


def format_views(views: int, lang: dict) -> str:
    multipliers = lang["ui"]["field"]["number_multiplier"]
    if views >= 1_000_000_000:
        return f"{round(views / 1_000_000_000)}{multipliers['billion']}"
    if views >= 1_000_000:
        return f"{round(views / 1_000_000)}{multipliers['million']}"
    if views >= 1_000:
        return f"{round(views / 1_000)}{multipliers['thousand']}"
    return f"{views:,}"


def format_time_ago(days_delta: float, lang: dict) -> str:
    td = lang["ui"]["field"]["time_delta"]
    days = int(days_delta)
    if days >= 365:
        years = floor(days / 365)
        return td["year"]["singular"] if years == 1 else td["year"]["plural"].format(years)
    if days >= 30:
        months = floor(days / 30)
        return td["month"]["singular"] if months == 1 else td["month"]["plural"].format(months)
    if days >= 1:
        return td["day"]["singular"] if days == 1 else td["day"]["plural"].format(days)
    hours = floor(days_delta * 24)
    if hours >= 1:
        return td["hour"]["singular"] if hours == 1 else td["hour"]["plural"].format(hours)
    minutes = floor(days_delta * 24 * 60)
    if minutes <= 1:
        return td["minute"].get("less_than", td["minute"].get("less_then", "just now"))
    return td["minute"]["plural"].format(minutes)


def find_urls(text: str) -> list[str]:
    return [m[0] for m in _URL_RE.findall(text)]


def extract_yt_id(url: str) -> str:
    query = urlparse(url)
    if query.hostname == "youtu.be":
        return query.path[1:]
    if query.hostname in {"www.youtube.com", "youtube.com", "music.youtube.com"}:
        with suppress(KeyError):
            return parse_qs(query.query)["list"][0]
        if query.path == "/watch":
            return parse_qs(query.query)["v"][0]
        if query.path.startswith("/watch/"):
            return query.path.split("/")[1]
        if query.path.startswith("/embed/"):
            return query.path.split("/")[2]
        if query.path.startswith("/v/"):
            return query.path.split("/")[2]
    return url
