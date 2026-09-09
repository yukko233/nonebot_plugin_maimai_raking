"""歌曲类型与宴谱标题相关的通用工具。"""

import re
from typing import Any, Optional, Tuple


_UTAGE_TITLE_PATTERN = re.compile(
    r"^\s*[\[［【](?P<marker>[^\]］】]+)[\]］】]\s*(?P<title>.+?)\s*$"
)


def is_utage_song(song: dict) -> bool:
    """判断歌曲是否为宴会场谱面。"""
    try:
        if int(song.get("id", 0)) >= 100000:
            return True
    except (TypeError, ValueError):
        pass

    basic_info = song.get("basic_info")
    genre = basic_info.get("genre") if isinstance(basic_info, dict) else ""
    chart_type = song.get("category") or song.get("chartType") or genre or ""
    return str(chart_type).strip().casefold() in {"utage", "宴会场"}


def song_difficulty_count(song: dict) -> int:
    """返回曲目实际提供的难度数量。"""
    for field in ("ds", "level"):
        values = song.get(field)
        if isinstance(values, (list, tuple)):
            return sum(value is not None and value != "" for value in values)
    return 0


def is_cooperative_utage_song(song: dict) -> bool:
    """双人协力宴谱在曲库中会提供 1P、2P 两个难度。"""
    return is_utage_song(song) and song_difficulty_count(song) >= 2


def split_utage_title(title: Any) -> Optional[Tuple[str, str]]:
    """把 ``[标签]原歌名`` 拆成标签和原歌名。"""
    if not isinstance(title, str):
        return None
    matched = _UTAGE_TITLE_PATTERN.match(title)
    if matched is None:
        return None
    return matched.group("marker").strip(), matched.group("title").strip()
