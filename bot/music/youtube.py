from __future__ import annotations

import logging
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, Future
from datetime import datetime
from re import findall
from typing import TYPE_CHECKING
from urllib.parse import urlencode
from urllib.request import urlopen, urlretrieve

from PIL import Image
from extcolors import extract_from_path
from yt_dlp import YoutubeDL

from bot.config import DEFAULT_PALETTE, THUMBNAIL_ERROR

if TYPE_CHECKING:
    from bot.music.player import GuildPlayer

log = logging.getLogger(__name__)

YDL_OPTIONS = {"format": "bestaudio/best", "noplaylist": True, "quiet": True, "no_warnings": True}

# Shared thread pool for parallel I/O work (8 workers for playlist batch fetching)
_pool = ThreadPoolExecutor(max_workers=8)


CACHE_MAX_AGE = 6 * 3600  # evict cache entries after 6 hours (matches YouTube audio URL expiry)


class YouTubeService:
    """Handles YouTube searching, metadata fetching, and in-memory caching."""

    def __init__(self) -> None:
        self.cache: dict[str, dict] = {}

    def evict_stale(self) -> None:
        """Remove cache entries older than CACHE_MAX_AGE seconds."""
        now = datetime.now().timestamp()
        stale = [
            vid_id for vid_id, entry in self.cache.items()
            if now - entry.get("_cached_at", 0) > CACHE_MAX_AGE
        ]
        for vid_id in stale:
            del self.cache[vid_id]
        if stale:
            log.info("Evicted %d stale cache entries", len(stale))

    def fetch_info(
        self,
        video_id: str,
        only_source: bool = False,
        refetch: bool = False,
    ) -> bool:
        # Return cached if we already have a valid source URL and aren't forcing refresh
        if not refetch and not only_source and video_id in self.cache and "source" in self.cache[video_id]:
            return True

        log.info("Fetching info for: %s", video_id)

        # Start scraping related videos in parallel with yt_dlp
        related_future: Future = _pool.submit(_scrape_related, video_id)

        vid = None
        try:
            with YoutubeDL(YDL_OPTIONS) as ydl:
                vid = ydl.extract_info(video_id, download=False)
        except Exception as e:
            log.warning("yt_dlp failed for %s: %r", video_id, e)

        audio_url = self._extract_audio_url(vid)
        if not audio_url:
            log.warning("No usable audio URL for %s", video_id)
            related_future.cancel()
            return False

        # Quick refresh of just the source URL
        if only_source and video_id in self.cache:
            self.cache[video_id]["source"] = audio_url
            related_future.cancel()
            return True

        # Build entry with metadata from yt_dlp
        entry = self._build_metadata(video_id, vid, audio_url)
        entry["_cached_at"] = datetime.now().timestamp()

        # Start palette extraction in parallel while we wait for related videos
        thumb = entry["thumbnail"]
        is_music = entry.pop("_is_music")
        palette_future: Future | None = None
        if thumb and thumb != THUMBNAIL_ERROR:
            palette_future = _pool.submit(_extract_palette, thumb, video_id, is_music)

        # Collect related videos (already running in parallel since before yt_dlp)
        try:
            entry["recomended_vids"] = related_future.result(timeout=10)
        except Exception:
            entry["recomended_vids"] = []

        # Collect palette
        if palette_future:
            try:
                entry["palette"] = palette_future.result(timeout=10)
            except Exception:
                entry["palette"] = DEFAULT_PALETTE
        else:
            entry["palette"] = DEFAULT_PALETTE

        self.cache[video_id] = entry
        return True

    @staticmethod
    def _extract_audio_url(vid: dict | None) -> str | None:
        if vid is None:
            return None
        audio_url = vid.get("url")
        if not audio_url:
            formats = vid.get("formats") or []
            audio_only = [
                f for f in formats
                if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")
            ]
            if audio_only:
                best = max(audio_only, key=lambda f: f.get("abr") or f.get("tbr") or 0)
                audio_url = best.get("url")
        return audio_url

    @staticmethod
    def _build_metadata(video_id: str, vid: dict | None, audio_url: str) -> dict:
        entry: dict = {"source": audio_url}

        if vid is None:
            entry.update(
                title="Unknown title", views=0, secs_length=0,
                publish_date=datetime.now().strftime("%Y%m%d"),
                channel_link="https://www.youtube.com",
                channel_name="Unknown channel",
                thumbnail=THUMBNAIL_ERROR,
                _is_music=False,
            )
        else:
            entry.update(
                title=vid.get("title") or "Unknown title",
                views=vid.get("view_count", 0),
                secs_length=int(vid.get("duration") or 0),
                publish_date=vid.get("upload_date") or datetime.now().strftime("%Y%m%d"),
                channel_link=vid.get("uploader_url") or vid.get("channel_url") or "https://www.youtube.com",
                channel_name=vid.get("uploader") or vid.get("channel") or "Unknown channel",
                thumbnail=vid.get("thumbnail") or THUMBNAIL_ERROR,
                _is_music=bool(vid.get("artist") or vid.get("album") or vid.get("track")),
            )

        return entry

    def search(self, query: str, disliked: set[str] | None = None) -> str | None:
        query_string = urlencode({"search_query": query})
        try:
            html = urlopen(f"http://www.youtube.com/results?{query_string}").read().decode()
            results = findall(r"watch\?v=(\S{11})", html)
            blocked = disliked or set()
            for vid_id in results:
                if vid_id not in blocked:
                    return vid_id
            return None
        except Exception:
            log.warning("YouTube search failed for: %s", query)
            return None

    def search_and_fetch(self, query: str, disliked: set[str] | None = None) -> str | None:
        if self.verify_id(query):
            video_id = query
        else:
            video_id = self.search(query, disliked)
            if video_id is None:
                return None
        if not self.fetch_info(video_id):
            return None
        return video_id

    @staticmethod
    def verify_id(video_id: str) -> bool:
        try:
            from requests import get as sync_get
            url = f"https://www.youtube.com/oembed?url=http://www.youtube.com/watch?v={video_id}"
            return sync_get(url, timeout=5).status_code == 200
        except Exception:
            return False

    def fetch_playlist(self, playlist_url: str) -> list[str]:
        """Extract video IDs from a YouTube playlist URL using yt_dlp (fast, flat extraction)."""
        opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "flat_playlist": True}
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(playlist_url, download=False)
            entries = info.get("entries") or []
            ids = [e["id"] for e in entries if e and e.get("id")]
            log.info("Extracted %d videos from playlist", len(ids))
            return ids
        except Exception as e:
            log.warning("Failed to extract playlist %s: %s", playlist_url, e)
            return []

    def fetch_many(self, video_ids: list[str]) -> list[str]:
        """Fetch info for multiple videos concurrently. Returns IDs in original order."""
        futures = [(vid, _pool.submit(self.fetch_info, vid)) for vid in video_ids]
        fetched = []
        for vid, future in futures:
            try:
                if future.result(timeout=30):
                    fetched.append(vid)
            except Exception:
                log.warning("Failed to fetch info for %s", vid)
        return fetched

    def find_autoplay(self, player: GuildPlayer, disliked: set[str] | None = None) -> bool:
        """Find autoplay based on all songs in queue + history for balanced selection."""
        all_songs = list(player.past_queue) + list(player.queue)
        if not all_songs:
            return False

        blocked = set(player.past_queue) | set(player.queue) | (disliked or set())
        candidates = Counter()

        for song_id in all_songs:
            if song_id not in self.cache:
                continue
            recs = self.cache[song_id].get("recomended_vids", [])
            if not recs and song_id == player.current:
                recs = _scrape_related(song_id)
                if recs:
                    self.cache[song_id]["recomended_vids"] = recs
            for rec_id in recs:
                if rec_id not in blocked:
                    candidates[rec_id] += 1

        if not candidates:
            current = player.current
            if current:
                recs = _scrape_related(current)
                for rec_id in recs:
                    if rec_id not in blocked:
                        candidates[rec_id] += 1

        for rec_id, _ in candidates.most_common():
            if self.fetch_info(rec_id):
                player.recommended_vid = rec_id
                log.info("Autoplay set to: %s (score: %d)", rec_id, candidates[rec_id])
                return True

        log.warning("No valid autoplay candidates found")
        return False


def _scrape_related(video_id: str) -> list[str]:
    """Scrape YouTube's watch page for related/suggested video IDs."""
    try:
        html = urlopen(f"https://www.youtube.com/watch?v={video_id}").read().decode()
        ids = findall(r'"videoId":"(\S{11})"', html)
        seen = set()
        result = []
        for vid_id in ids:
            if vid_id != video_id and vid_id not in seen:
                seen.add(vid_id)
                result.append(vid_id)
            if len(result) >= 15:
                break
        log.info("Scraped %d related videos for %s", len(result), video_id)
        return result
    except Exception as e:
        log.warning("Failed to scrape related videos for %s: %s", video_id, e)
        return []


def _extract_palette(thumbnail_url: str, video_id: str = "", crop_square: bool = False) -> tuple:
    safe_id = video_id.replace("/", "_").replace("\\", "_") or "tmp"
    img_path = os.path.join("Cache", f"img_{safe_id}.png")
    urlretrieve(thumbnail_url, img_path)

    img = Image.open(img_path)

    # For YouTube Music videos, crop the square album art from center
    # so the palette colors come from the actual artwork, not the sidebars
    if crop_square and img.width > img.height:
        left = (img.width - img.height) // 2
        img = img.crop((left, 0, left + img.height, img.height))

    ratio = 300 / float(img.size[0])
    new_height = int(float(img.size[1]) * ratio)
    img = img.resize((300, new_height), Image.Resampling.LANCZOS)
    img.save(img_path)

    colors = extract_from_path(img_path, tolerance=16, limit=3)
    return (colors[0][0][0], colors[0][1][0], colors[0][2][0])
