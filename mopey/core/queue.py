"""
SongQueue — a simple bounded queue for one guild.

Keeping this as its own class means the GuildPlayer doesn't
manipulate raw lists directly, and queue rules (max size, etc.)
are enforced in one place.
"""

from .song import Song

MAX_QUEUE_SIZE = 50


class SongQueue:

    def __init__(self, max_size: int = MAX_QUEUE_SIZE):
        self._songs: list[Song] = []
        self.max_size = max_size

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def add(self, song: Song) -> bool:
        """Add a song to the end of the queue. Returns False if full."""
        if len(self._songs) >= self.max_size:
            return False
        self._songs.append(song)
        return True

    def pop_next(self) -> Song | None:
        """Remove and return the next song, or None if empty."""
        return self._songs.pop(0) if self._songs else None

    def peek_next(self) -> Song | None:
        """Return the next song without removing it."""
        return self._songs[0] if self._songs else None

    def remove_at(self, position: int) -> Song | None:
        """
        Remove the song at 1-based position.
        Returns the removed Song, or None if position is invalid.
        """
        index = position - 1
        if 0 <= index < len(self._songs):
            return self._songs.pop(index)
        return None

    def move_to_front(self, position: int) -> Song | None:
        """
        Move the song at 1-based position to the front of the queue.
        Returns the song, or None if position is invalid.
        """
        index = position - 1
        if 0 <= index < len(self._songs):
            song = self._songs.pop(index)
            self._songs.insert(0, song)
            return song
        return None

    def clear(self):
        self._songs.clear()

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def is_empty(self) -> bool:
        return len(self._songs) == 0

    def is_full(self) -> bool:
        return len(self._songs) >= self.max_size

    def __len__(self) -> int:
        return len(self._songs)

    def __iter__(self):
        return iter(self._songs)

    def __getitem__(self, index):
        return self._songs[index]
