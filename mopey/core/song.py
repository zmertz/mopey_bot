"""
Song dataclass — the single shared data contract between sources, the queue,
the player, and the UI. Both YouTube and Plex produce one of these.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Song:
    title: str
    url: str            # The actual streamable audio URL
    link: str           # The original link/identifier (YouTube URL, Plex stream URL)
    duration: int       # Seconds

    # Optional metadata — Plex provides these; YouTube typically doesn't
    artist: Optional[str] = None
    album: Optional[str] = None
    thumbnail: Optional[str] = None

    def to_dict(self) -> dict:
        """Convenience for any legacy code paths that expect a plain dict."""
        return {
            "title": self.title,
            "url": self.url,
            "link": self.link,
            "duration": self.duration,
            "artist": self.artist,
            "album": self.album,
            "thumbnail": self.thumbnail,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Song":
        return cls(
            title=d.get("title", "Unknown Title"),
            url=d.get("url", ""),
            link=d.get("link", d.get("url", "")),
            duration=d.get("duration", 0),
            artist=d.get("artist"),
            album=d.get("album"),
            thumbnail=d.get("thumbnail"),
        )
