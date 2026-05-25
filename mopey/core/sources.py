"""
Audio source abstraction.

AudioSource defines the contract: search() and resolve().
YouTubeSource and PlexSource implement it.

The GuildPlayer and commands never branch on "is this Plex or YouTube?" —
they just call these methods and get back Song objects.
"""

import asyncio
from abc import ABC, abstractmethod
from typing import Optional

import yt_dlp
from plexapi.server import PlexServer

from .song import Song
from ..utils.log import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class AudioSource(ABC):

    @abstractmethod
    async def search(self, query: str, limit: int = 3) -> list[Song]:
        """
        Search for tracks matching query.
        Returns up to `limit` Song objects (url/link may be unresolved at this stage).
        """
        ...

    @abstractmethod
    async def resolve(self, song: Song) -> Song:
        """
        Given a Song (possibly with an unresolved stream URL), return a new Song
        with a fully playable `url` filled in.

        For Plex this is a no-op (stream URL is already known at search time).
        For YouTube we need to extract the real audio stream URL from the page URL.
        """
        ...


# ---------------------------------------------------------------------------
# YouTube
# ---------------------------------------------------------------------------

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "playlist_items": "1",
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
}


def _is_url(query: str) -> bool:
    return query.startswith("http://") or query.startswith("https://")


class YouTubeSource(AudioSource):

    def __init__(self):
        self._ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

    async def search(self, query: str, limit: int = 3) -> list[Song]:
        """
        Search YouTube and return up to `limit` results.
        If `query` is a direct URL, resolve it immediately and return a single Song
        rather than treating it as a search string (which would break playlist URLs).
        """
        if _is_url(query):
            log.info(f"YouTube direct URL detected, resolving: {query}")
            song = await self.resolve(Song(title="", url="", link=query, duration=0))
            return [song]

        log.info(f"YouTube search: {query!r} (limit={limit})")
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(
            None,
            lambda: self._ytdl.extract_info(f"ytsearch{limit}:{query}", download=False)
        )
        entries = data.get("entries", [])
        songs = []
        for entry in entries[:limit]:
            songs.append(Song(
                title=entry.get("title", "Unknown Title"),
                url=entry.get("url", ""),
                link=entry.get("webpage_url", ""),
                duration=entry.get("duration", 0),
                thumbnail=entry.get("thumbnail"),
            ))
        log.info(f"YouTube search {query!r} → {len(songs)} result(s)")
        return songs

    async def resolve(self, song: Song) -> Song:
        """
        Re-extract to get a fresh, playable stream URL from the YouTube page URL.
        YouTube stream URLs expire, so we always re-resolve just before playing.
        """
        log.debug(f"YouTube resolving stream URL for: {song.link}")
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(
            None,
            lambda: self._ytdl.extract_info(song.link, download=False)
        )
        if "entries" in data:
            data = data["entries"][0]

        resolved = Song(
            title=data.get("title", song.title),
            url=data["url"],
            link=song.link,
            duration=data.get("duration", song.duration),
            thumbnail=data.get("thumbnail", song.thumbnail),
            artist=song.artist,
            album=song.album,
        )
        log.debug(f"YouTube resolved: {resolved.title!r} ({resolved.duration}s)")
        return resolved


# ---------------------------------------------------------------------------
# Plex
# ---------------------------------------------------------------------------

class PlexSource(AudioSource):

    def __init__(self, base_url: str, token: str):
        self._base_url = base_url
        self._token = token
        self._plex: Optional[PlexServer] = None

    def _get_connection(self) -> Optional[PlexServer]:
        if self._plex:
            return self._plex
        try:
            self._plex = PlexServer(self._base_url, self._token)
            log.info("Connected to Plex server.")
        except Exception as e:
            log.error(f"Failed to connect to Plex: {e}", exc_info=True)
            self._plex = None
        return self._plex

    def _build_stream_url(self, track) -> str:
        media_part = track.media[0].parts[0]
        return f"{self._base_url}{media_part.key}?X-Plex-Token={self._token}"

    def _track_to_song(self, track) -> Optional[Song]:
        try:
            stream_url = self._build_stream_url(track)
            return Song(
                title=track.title,
                url=stream_url,
                link=stream_url,   # For Plex, link == url (no separate page URL)
                duration=track.duration // 1000 if track.duration else 0,
                artist=getattr(track, "grandparentTitle", None),
                album=getattr(track, "parentTitle", None),
                thumbnail=getattr(track, "artUrl", None),
            )
        except Exception as e:
            log.warning(f"Skipping malformed Plex track ({getattr(track, 'title', '?')}): {e}")
            return None

    async def search(self, query: str, limit: int = 3) -> list[Song]:
        """Search Plex music library. Runs synchronous PlexAPI call in executor."""
        loop = asyncio.get_event_loop()

        def _search():
            conn = self._get_connection()
            if not conn:
                return []
            try:
                return conn.search(query, mediatype="track")
            except Exception as e:
                log.error(f"Plex search failed for query {query!r}: {e}", exc_info=True)
                return []

        results = await loop.run_in_executor(None, _search)
        songs = []
        for track in results[:limit]:
            song = self._track_to_song(track)
            if song:
                songs.append(song)

        log.info(f"Plex search {query!r} → {len(songs)} result(s)")
        return songs

    async def resolve(self, song: Song) -> Song:
        """Plex stream URLs are already fully resolved at search time — nothing to do."""
        return song