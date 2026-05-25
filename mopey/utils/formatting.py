"""
Pure formatting helpers. No Discord or bot imports — easy to test in isolation.
"""


def format_time(seconds: int) -> str:
    """Format seconds into M:SS string. e.g. 185 -> '3:05'"""
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def build_progress_bar(elapsed: int, total: int, length: int = 20) -> str:
    """Build a text progress bar. e.g. '[■■■■─ ─ ─ ─ ─ ─ ─ ─ ]'"""
    progress = int((elapsed / total) * length) if total else 0
    bar = "■" * progress + "─ " * (length - progress)
    return f"[{bar}]"


def format_song_line(title: str, duration: int, artist: str = None, album: str = None) -> str:
    """
    Build a human-readable song description line.
    e.g. 'Song Title - Artist - Album - (*3:45*)'
    """
    parts = [title]
    if artist:
        parts.append(artist)
    if album:
        parts.append(album)
    parts.append(f"(*{format_time(duration)}*)")
    return " - ".join(parts)
