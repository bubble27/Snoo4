from __future__ import annotations

import re
import unicodedata
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


# Approximate character widths for Discord's font (gg sans / proportional sans-serif).
# Values are relative units where a typical lowercase letter = 1.0.
_CHAR_WIDTHS: dict[str, float] = {}

# Narrow characters (~0.4)
for ch in "iIl|!:;.,'\u2019\u2018\u201a":
    _CHAR_WIDTHS[ch] = 0.4

# Slightly narrow (~0.6)
for ch in "1jtfrJ()[]{}/ \"":
    _CHAR_WIDTHS[ch] = 0.6

# Normal width (~0.85) — most lowercase and digits
for ch in "abcdeghknopqsuvxyz023456789":
    _CHAR_WIDTHS[ch] = 0.85

# Slightly wide (~1.0) — uppercase, some lowercase
for ch in "ABCDEFGHKLNOPQRSTUVXYZmw":
    _CHAR_WIDTHS[ch] = 1.0

# Wide (~1.15)
for ch in "MW@#%&":
    _CHAR_WIDTHS[ch] = 1.15

# Special
_CHAR_WIDTHS[" "] = 0.45
_CHAR_WIDTHS["\u2014"] = 1.0  # em dash
_CHAR_WIDTHS["\u2013"] = 0.7  # en dash
_CHAR_WIDTHS["-"] = 0.5


def _char_width(ch: str) -> float:
    """Get approximate display width for a single character."""
    if ch in _CHAR_WIDTHS:
        return _CHAR_WIDTHS[ch]
    eaw = unicodedata.east_asian_width(ch)
    if eaw in ("W", "F"):
        return 1.8  # CJK / fullwidth
    if unicodedata.category(ch).startswith("M"):
        return 0.0  # combining marks
    return 0.85  # default


def display_width(text: str) -> float:
    """Calculate approximate display width of text in Discord's font."""
    return sum(_char_width(ch) for ch in text)


def truncate(text: str, max_width: float = 36.0, max_chars: int = 55) -> str:
    """Truncate text to fit within max_width display units, adding ... if needed.
    Also enforces a hard character limit as a safety net."""
    if len(text) <= 3:
        return text
    if display_width(text) <= max_width and len(text) <= max_chars:
        return text
    ellipsis_w = display_width("...")
    w = 0.0
    for i, ch in enumerate(text):
        w += _char_width(ch)
        if w > max_width - ellipsis_w or i >= max_chars - 3:
            return text[:i] + "..."
    return text


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
