"""API 模块 - 对接水鱼 API 和别名 API"""
import asyncio
import aiosqlite
import httpx
import json
import sqlite3
import unicodedata
from opencc import OpenCC
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple
from nonebot.log import logger

from .oauth import OAuthError, OAuthManager, OAuthQuotaExceeded
from .lxns_oauth import (
    LxnsOAuthConsentRequired,
    LxnsOAuthManager,
    LxnsOAuthQuotaExceeded,
)
from .song_utils import is_utage_song, split_utage_title


_JAPANESE_TO_TRADITIONAL = OpenCC("jp2t.json")
_TRADITIONAL_TO_SIMPLIFIED = OpenCC("t2s.json")


def _normalize_cjk_variants(value: str) -> str:
    """将日文新字体、繁体字统一为简体字，供搜索匹配使用。"""
    traditional = _JAPANESE_TO_TRADITIONAL.convert(value)
    return _TRADITIONAL_TO_SIMPLIFIED.convert(traditional)


@dataclass(frozen=True)
class SongSearchResult:
    """歌曲搜索结果及其匹配信息。"""

    song: dict
    score: int
    matched_by: str


class MaimaiAPI:
    """舞萌 API 客户端"""

    _LXNS_VERSION_WEIGHTS = {
        "maimai": 10,
        "maimai PLUS": 20,
        "maimai GreeN": 30,
        "maimai GreeN PLUS": 40,
        "maimai ORANGE": 50,
        "maimai ORANGE PLUS": 60,
        "maimai PiNK": 70,
        "maimai PiNK PLUS": 80,
        "maimai MURASAKi": 90,
        "maimai MURASAKi PLUS": 100,
        "maimai MiLK": 110,
        "MiLK PLUS": 120,
        "maimai FiNALE": 130,
        "maimai でらっくす": 140,
        "maimai でらっくす Splash": 160,
        "maimai でらっくす UNiVERSE": 180,
        "maimai でらっくす FESTiVAL": 190,
        "maimai でらっくす BUDDiES": 200,
        "maimai でらっくす PRiSM": 210,
        "maimai でらっくす PRiSM PLUS": 220,
    }

    def __init__(
        self,
        oauth: OAuthManager,
        lxns_oauth: Optional[LxnsOAuthManager] = None,
    ):
        """初始化 API 客户端

        Args:
            oauth: 水鱼 OAuth 管理器
            lxns_oauth: 落雪 OAuth 管理器
        """
        import nonebot_plugin_localstore as store

        self.oauth = oauth
        self.lxns_oauth = lxns_oauth
        self.base_url = "https://www.diving-fish.com/api/maimaidxprober"
        lxns_base_url = getattr(lxns_oauth, "base_url", "https://maimai.lxns.net")
        self.lxns_base_url = f"{lxns_base_url.rstrip('/')}/api/v0/user/maimai/player"
        self.alias_url = "https://www.yuzuchan.moe/api/maimaidx/maimaidxalias"
        self.alias_lxns_url = "https://maimai.lxns.net/api/v0/maimai/alias/list"
        self.alias_dxrating_url = "https://miruku.dxrating.net/api/v1/aliases"
        # ID 映射规则发生变化时必须使旧缓存失效，避免继续使用未转换的落雪 ID。
        self.alias_cache_version = 3

        # 缓存数据
        self.music_data: List[dict] = []
        self.alias_data: List[dict] = []

        # 本地缓存数据库路径
        self.cache_dir: Path = store.get_plugin_cache_dir()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_db_file: Path = self.cache_dir / "cache.db"
        self._cache_initialized: bool = False

        # HTTP 客户端
        self.client = httpx.AsyncClient(timeout=30.0)

        # 自定义别名缓存
        self.custom_alias_map: Dict[int, List[str]] = {}

    async def init(self):
        """异步初始化缓存数据库（需在事件循环启动后调用）"""
        if self._cache_initialized:
            return
        await self._init_cache_database()
        self._cache_initialized = True

    async def _init_cache_database(self):
        """初始化缓存数据库表结构"""
        async with aiosqlite.connect(self.cache_db_file) as db:
            db.row_factory = sqlite3.Row
            await db.execute("""
                CREATE TABLE IF NOT EXISTS alias_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    data TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS cover_cache (
                    song_id INTEGER PRIMARY KEY,
                    cover_data BLOB NOT NULL,
                    cached_at TEXT NOT NULL
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS cover_thumbnail (
                    song_id INTEGER PRIMARY KEY,
                    thumbnail BLOB NOT NULL,
                    cached_at TEXT NOT NULL
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS help_image_cache (
                    is_admin INTEGER PRIMARY KEY,
                    data BLOB NOT NULL,
                    cached_at TEXT NOT NULL
                )
            """)
            await db.commit()
            logger.info("API 缓存数据库初始化完成")

    def is_utage_chart(self, song_id: int) -> bool:
        """检查是否为宴谱（ID为六位数的谱面）"""
        return song_id >= 100000

    async def load_music_data(self):
        """加载歌曲数据"""
        try:
            url = f"{self.base_url}/music_data"
            response = await self.client.get(url)

            if response.status_code == 200:
                self.music_data = response.json()
                logger.info(f"成功加载 {len(self.music_data)} 首歌曲数据")
            else:
                logger.error(f"加载歌曲数据失败: {response.status_code}")
        except Exception as e:
            logger.error(f"加载歌曲数据时出错: {e}")

    @staticmethod
    def _normalize_title(title: Any) -> str:
        """规范化歌名，用于匹配 DXRating 返回的歌名。"""
        if not isinstance(title, str):
            return ""
        normalized = unicodedata.normalize("NFKC", title).casefold().strip()
        normalized = _normalize_cjk_variants(normalized)
        return "".join(char for char in normalized if not char.isspace())

    @staticmethod
    def _normalize_search_text(value: Any, compact: bool = False) -> str:
        """规范化搜索文本，兼容全角字符、大小写和常见分隔符。"""
        if value is None:
            return ""
        normalized = unicodedata.normalize("NFKC", str(value)).casefold().strip()
        normalized = _normalize_cjk_variants(normalized)
        if compact:
            return "".join(
                char
                for char in normalized
                if not char.isspace()
                and not unicodedata.category(char).startswith("P")
            )
        return " ".join(normalized.split())

    @classmethod
    def _score_search_term(
        cls, query: str, candidate: str, field: str
    ) -> Optional[Tuple[int, str]]:
        """计算歌曲名或别名与查询词的匹配分数。"""
        query_normalized = cls._normalize_search_text(query)
        candidate_normalized = cls._normalize_search_text(candidate)
        query_compact = cls._normalize_search_text(query, compact=True)
        candidate_compact = cls._normalize_search_text(candidate, compact=True)
        if not query_normalized or not candidate_normalized:
            return None

        title_bonus = 20 if field == "title" else 0
        if query_normalized == candidate_normalized:
            return 1000 + title_bonus, f"{field}_exact"
        if query_compact == candidate_compact:
            return 940 + title_bonus, f"{field}_normalized"

        # 单字符查询只接受精确匹配，避免一个字匹配到大量无关歌曲。
        if len(query_compact) < 2:
            return None
        if candidate_normalized.startswith(query_normalized):
            return 840 + title_bonus, f"{field}_prefix"
        if candidate_compact.startswith(query_compact):
            return 820 + title_bonus, f"{field}_normalized_prefix"
        if query_normalized in candidate_normalized:
            return 740 + title_bonus, f"{field}_contains"
        if query_compact in candidate_compact:
            return 720 + title_bonus, f"{field}_normalized_contains"

        similarity = SequenceMatcher(None, query_compact, candidate_compact).ratio()
        if similarity >= 0.72:
            return 300 + int(similarity * 100) + title_bonus, f"{field}_fuzzy"
        return None

    @classmethod
    def _is_post_finale_version(cls, song: dict) -> bool:
        """判断歌曲版本是否晚于 Finale。

        水鱼 music_data 的 basic_info.from 使用原始版本名称；这里的权重与
        maittx 的版本判断保持一致。未知版本按未知版本处理，启用 DX ID 规则。
        """
        basic_info = song.get("basic_info")
        if not isinstance(basic_info, dict):
            return True
        raw_version = basic_info.get("from")
        if raw_version is None or str(raw_version).strip() == "":
            raw_version = basic_info.get("version")
        version_weight = cls._LXNS_VERSION_WEIGHTS.get(str(raw_version), 999)
        return version_weight > 130

    @staticmethod
    def _normalize_song_id_for_lxns(song_id: Any, is_post_finale: bool) -> str:
        """将水鱼歌曲 ID 转换为落雪资源/普通歌曲 ID 格式。

        该转换只用于与落雪侧的 ID 表示进行匹配；水鱼 music_data 中的原始
        Song.id 不会被修改。宴会场歌曲由调用方保留自己的 6 位歌曲 ID。
        """
        normalized_id = str(song_id).strip()
        if not is_post_finale:
            return normalized_id

        if len(normalized_id) == 6 and normalized_id.startswith("100"):
            stripped = normalized_id[3:]
            try:
                return str(int(stripped))
            except ValueError:
                return stripped
        if len(normalized_id) == 5 and normalized_id.startswith("10"):
            stripped = normalized_id[2:]
            try:
                return str(int(stripped))
            except ValueError:
                return stripped
        if len(normalized_id) >= 5:
            return normalized_id[1:]
        return normalized_id

    @staticmethod
    def _as_alias_entries(data: Any) -> List[dict]:
        """从不同数据源的响应中提取别名条目。"""
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if not isinstance(data, dict):
            return []
        for key in ("content", "aliases", "data"):
            entries = data.get(key)
            if isinstance(entries, list):
                return [item for item in entries if isinstance(item, dict)]
        return []

    @staticmethod
    def _add_aliases(alias_map: Dict[int, List[str]], song_id: Any, aliases: Any) -> int:
        """将一首歌的别名合并到统一映射中。"""
        try:
            song_id = int(song_id)
        except (TypeError, ValueError):
            return 0
        if not isinstance(aliases, list):
            return 0

        target = alias_map.setdefault(song_id, [])
        existing = {alias.casefold() for alias in target if isinstance(alias, str)}
        added_count = 0
        for alias in aliases:
            if not isinstance(alias, str):
                continue
            alias = alias.strip()
            if not alias or alias.casefold() in existing:
                continue
            target.append(alias)
            existing.add(alias.casefold())
            added_count += 1
        return added_count

    def _build_song_title_map(self) -> Dict[str, List[int]]:
        """建立规范化歌名到歌曲 ID 的映射。"""
        title_map: Dict[str, List[int]] = {}
        for song in self.music_data:
            try:
                song_id = int(song["id"])
            except (KeyError, TypeError, ValueError):
                continue
            title = self._normalize_title(song.get("title"))
            if title:
                title_map.setdefault(title, []).append(song_id)
        return title_map

    def _build_lxns_song_id_map(self) -> Dict[str, List[int]]:
        """建立落雪歌曲 ID 到水鱼歌曲 ID 的映射。

        落雪普通歌曲使用单一歌曲 ID 表示 SD/DX 谱面；水鱼则可能分别维护
        SD ID 和 DX ID。因此同一个落雪 ID 可能对应多个水鱼 ID，必须全部
        保存，不能只保留最后一个映射。
        """
        lxns_id_map: Dict[str, List[int]] = {}
        for song in self.music_data:
            if not isinstance(song, dict):
                continue
            try:
                water_song_id = int(song["id"])
            except (KeyError, TypeError, ValueError):
                continue

            # 落雪别名接口对宴会场歌曲使用歌曲自身的 6 位 ID；资源 URL 的
            # 特殊转换不能套用到这里。
            if self.is_utage_chart(water_song_id):
                lxns_song_id = str(water_song_id)
            else:
                lxns_song_id = self._normalize_song_id_for_lxns(
                    water_song_id,
                    self._is_post_finale_version(song),
                )

            mapped_ids = lxns_id_map.setdefault(lxns_song_id, [])
            if water_song_id not in mapped_ids:
                mapped_ids.append(water_song_id)
        return lxns_id_map

    def _merge_yuzu_aliases(self, data: Any, alias_map: Dict[int, List[str]]) -> int:
        count = 0
        for item in self._as_alias_entries(data):
            song_id = item.get("SongID", item.get("song_id"))
            aliases = item.get("Alias", item.get("aliases"))
            count += self._add_aliases(alias_map, song_id, aliases)
        return count

    def _merge_lxns_aliases(self, data: Any, alias_map: Dict[int, List[str]]) -> int:
        """合并落雪别名，并将落雪 ID 转换回水鱼歌曲 ID。"""
        lxns_id_map = self._build_lxns_song_id_map()
        count = 0
        unmatched_count = 0
        for item in self._as_alias_entries(data):
            raw_song_id = item.get("song_id", item.get("SongID"))
            try:
                lxns_song_id = str(int(raw_song_id))
            except (TypeError, ValueError):
                lxns_song_id = str(raw_song_id or "").strip()

            water_song_ids = lxns_id_map.get(lxns_song_id, [])
            if not water_song_ids:
                unmatched_count += 1
                continue

            for water_song_id in water_song_ids:
                count += self._add_aliases(
                    alias_map, water_song_id, item.get("aliases", item.get("Alias"))
                )

        if unmatched_count:
            logger.info(f"落雪别名中有 {unmatched_count} 条歌曲 ID 未匹配到水鱼歌曲")
        return count

    def _merge_dxrating_aliases(self, data: Any, alias_map: Dict[int, List[str]]) -> int:
        """合并 DXRating 别名；该接口的 song_id 当前实际返回歌曲标题。"""
        title_map = self._build_song_title_map()
        known_ids = {
            int(song["id"])
            for song in self.music_data
            if isinstance(song, dict) and str(song.get("id", "")).isdigit()
        }
        count = 0
        for item in self._as_alias_entries(data):
            raw_song_id = item.get("song_id", item.get("SongID"))
            song_ids: List[int] = []
            if isinstance(raw_song_id, (int, float)) and not isinstance(raw_song_id, bool):
                if int(raw_song_id) in known_ids:
                    song_ids = [int(raw_song_id)]
            else:
                raw_song_id = str(raw_song_id or "").strip()
                if raw_song_id.isdigit() and int(raw_song_id) in known_ids:
                    song_ids = [int(raw_song_id)]
                else:
                    song_ids = title_map.get(self._normalize_title(raw_song_id), [])

            for song_id in song_ids:
                count += self._add_aliases(
                    alias_map, song_id, [item.get("name")]
                )
        return count

    async def _fetch_alias_sources(self) -> Dict[str, Any]:
        """并发获取三个别名数据源，单个数据源失败不影响其它来源。"""
        sources = {
            "柚子": self.alias_url,
            "落雪": self.alias_lxns_url,
            "DXRating": self.alias_dxrating_url,
        }

        async def fetch(name: str, url: str):
            try:
                response = await self.client.get(url)
                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}")
                return name, response.json(), None
            except Exception as e:
                return name, None, e

        results = await asyncio.gather(
            *(fetch(name, url) for name, url in sources.items())
        )
        return {name: (data, error) for name, data, error in results}

    async def _load_alias_data_from_network(self) -> List[dict]:
        """从三源合并别名数据，统一返回现有缓存格式。"""
        if not self.music_data:
            await self.load_music_data()

        results = await self._fetch_alias_sources()
        alias_map: Dict[int, List[str]] = {}
        merge_handlers = {
            "柚子": self._merge_yuzu_aliases,
            "落雪": self._merge_lxns_aliases,
            "DXRating": self._merge_dxrating_aliases,
        }
        success_count = 0
        for name, (data, error) in results.items():
            if error is not None:
                logger.warning(f"获取{name}别名数据失败: {error}")
                continue
            try:
                alias_count = merge_handlers[name](data, alias_map)
                success_count += 1
                logger.info(f"{name}别名数据加载完成，合并 {alias_count} 条别名")
            except Exception as e:
                logger.warning(f"处理{name}别名数据失败: {e}")

        if not success_count or not alias_map:
            return []
        return [
            {"SongID": song_id, "Alias": aliases}
            for song_id, aliases in alias_map.items()
            if aliases
        ]

    async def _save_alias_cache(self):
        """保存三源合并后的别名缓存。"""
        from datetime import datetime

        cache_payload = {
            "version": self.alias_cache_version,
            "data": self.alias_data,
        }
        data_json = json.dumps(cache_payload, ensure_ascii=False)
        updated_at = datetime.now().isoformat()
        async with aiosqlite.connect(self.cache_db_file) as db:
            await db.execute("DELETE FROM alias_cache")
            await db.execute(
                "INSERT INTO alias_cache (data, updated_at) VALUES (?, ?)",
                (data_json, updated_at),
            )
            await db.commit()

    async def load_alias_data(self):
        """加载别名数据（优先从数据库缓存加载）。"""
        cached_alias_data = None
        try:
            async with aiosqlite.connect(self.cache_db_file) as db:
                db.row_factory = sqlite3.Row
                cursor = await db.execute(
                    "SELECT data FROM alias_cache ORDER BY id DESC LIMIT 1"
                )
                row = await cursor.fetchone()
                if row:
                    cached = json.loads(row["data"])
                    if (
                        isinstance(cached, dict)
                        and cached.get("version") == self.alias_cache_version
                        and isinstance(cached.get("data"), list)
                    ):
                        self.alias_data = cached["data"]
                        logger.info(f"从数据库缓存加载 {len(self.alias_data)} 条别名数据")
                        return
                    if isinstance(cached, list):
                        cached_alias_data = cached
                        self.alias_data = cached
                        logger.info("检测到旧版别名缓存，将更新为三源合并数据")
        except Exception as e:
            logger.warning(f"加载数据库别名缓存失败: {e}，将从API获取")

        try:
            merged_alias_data = await self._load_alias_data_from_network()
            if merged_alias_data:
                self.alias_data = merged_alias_data
                await self._save_alias_cache()
                logger.info(f"成功加载并缓存 {len(self.alias_data)} 条三源别名数据")
            elif cached_alias_data is None:
                self.alias_data = []
        except Exception as e:
            logger.error(f"加载三源别名数据时出错: {e}")
            if cached_alias_data is None:
                self.alias_data = []

    async def load_alias_data_force(self):
        """强制从网络重新加载三源别名数据（用于定时更新）。"""
        try:
            logger.info("正在从网络强制更新三源别名数据...")
            merged_alias_data = await self._load_alias_data_from_network()
            if not merged_alias_data:
                logger.warning("三源别名数据均未加载到有效内容，保留现有缓存")
                return

            self.alias_data = merged_alias_data
            try:
                await self._save_alias_cache()
                logger.info(f"强制更新并缓存 {len(self.alias_data)} 条三源别名数据")
            except Exception as e:
                logger.error(f"保存三源别名缓存失败: {e}")
                logger.info(f"强制更新 {len(self.alias_data)} 条三源别名数据（未缓存）")
        except Exception as e:
            logger.error(f"强制更新三源别名数据时出错: {e}")

    # ==================== 自定义别名处理 ====================

    def _equals_ignore_case(self, a: str, b: str) -> bool:
        return a.lower() == b.lower()

    def _ensure_alias_entry(self, song_id: int) -> List[str]:
        if self.alias_data is None:
            self.alias_data = []
        song_id = int(song_id)
        for item in self.alias_data:
            try:
                current_song_id = int(item.get("SongID"))
            except (ValueError, TypeError):
                continue
            if current_song_id == song_id:
                alias_list = item.get("Alias")
                if not isinstance(alias_list, list):
                    alias_list = []
                    item["Alias"] = alias_list
                return alias_list
        new_item = {"SongID": song_id, "Alias": []}
        self.alias_data.append(new_item)
        return new_item["Alias"]

    def set_custom_aliases(self, custom_aliases: Dict[int, List[str]]):
        """覆盖自定义别名映射并同步到 alias_data"""
        self.custom_alias_map = {}
        if not custom_aliases:
            return
        for song_id, aliases in custom_aliases.items():
            if not aliases:
                continue
            normalized_aliases: List[str] = []
            for alias in aliases:
                if not isinstance(alias, str):
                    continue
                alias_str = alias.strip()
                if not alias_str:
                    continue
                if any(self._equals_ignore_case(existing, alias_str) for existing in normalized_aliases):
                    continue
                normalized_aliases.append(alias_str)
                alias_list = self._ensure_alias_entry(song_id)
                if not any(self._equals_ignore_case(existing, alias_str) for existing in alias_list):
                    alias_list.append(alias_str)
            if normalized_aliases:
                self.custom_alias_map[int(song_id)] = normalized_aliases

    def add_custom_alias(self, song_id: int, alias: str):
        """向缓存中新增自定义别名"""
        if not isinstance(alias, str):
            return
        alias_str = alias.strip()
        if not alias_str:
            return
        song_id = int(song_id)
        alias_list = self._ensure_alias_entry(song_id)
        if not any(self._equals_ignore_case(existing, alias_str) for existing in alias_list):
            alias_list.append(alias_str)
        custom_list = self.custom_alias_map.setdefault(song_id, [])
        if not any(self._equals_ignore_case(existing, alias_str) for existing in custom_list):
            custom_list.append(alias_str)

    def remove_custom_alias(self, song_id: int, alias: str):
        """从缓存中移除自定义别名"""
        if not isinstance(alias, str):
            return
        alias_str = alias.strip()
        if not alias_str:
            return
        song_id = int(song_id)
        if song_id in self.custom_alias_map:
            self.custom_alias_map[song_id] = [
                existing for existing in self.custom_alias_map[song_id]
                if not self._equals_ignore_case(existing, alias_str)
            ]
            if not self.custom_alias_map[song_id]:
                del self.custom_alias_map[song_id]
        if not self.alias_data:
            return
        for item in self.alias_data:
            try:
                current_song_id = int(item.get("SongID"))
            except (ValueError, TypeError):
                continue
            if current_song_id != song_id:
                continue
            alias_list = item.get("Alias")
            if not isinstance(alias_list, list):
                return
            item["Alias"] = [
                existing for existing in alias_list
                if not isinstance(existing, str) or not self._equals_ignore_case(existing, alias_str)
            ]
            return

    def get_aliases_for_song(self, song_id: int) -> List[str]:
        """获取指定歌曲的所有别名（包含自定义别名）"""
        if not self.alias_data:
            return []
        song_id = int(song_id)
        for item in self.alias_data:
            try:
                current_song_id = int(item.get("SongID"))
            except (ValueError, TypeError):
                continue
            if current_song_id == song_id:
                alias_list = item.get("Alias")
                if isinstance(alias_list, list):
                    return [alias for alias in alias_list if isinstance(alias, str)]
                return []
        return []

    def find_song_id_by_alias(self, alias: str) -> Optional[int]:
        """根据别名查找歌曲 ID"""
        if not alias or not self.alias_data:
            return None
        alias_lower = alias.strip().lower()
        if not alias_lower:
            return None
        for item in self.alias_data:
            alias_list = item.get("Alias")
            if not isinstance(alias_list, list):
                continue
            for existing in alias_list:
                if not isinstance(existing, str):
                    continue
                if existing.lower() == alias_lower:
                    try:
                        return int(item.get("SongID"))
                    except (ValueError, TypeError):
                        continue
        return None

    @staticmethod
    def _lxns_level_index(value: Any) -> int:
        if isinstance(value, int) and not isinstance(value, bool):
            return max(0, min(4, value))
        text = str(value or "").strip().casefold().replace("：", ":")
        names = {
            "basic": 0,
            "advanced": 1,
            "expert": 2,
            "master": 3,
            "remaster": 4,
            "re:master": 4,
            "re master": 4,
        }
        if text in names:
            return names[text]
        try:
            return max(0, min(4, int(text)))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _lxns_song_type(value: Any) -> str:
        text = str(value or "").strip().casefold()
        return {
            "standard": "SD",
            "sd": "SD",
            "dx": "DX",
            "deluxe": "DX",
            "utage": "DX",
        }.get(text, text.upper())

    def _find_lxns_song(self, score: dict) -> Optional[dict]:
        """将落雪统一歌曲 ID 映射到水鱼 music_data 中的歌曲。"""
        raw_id = score.get("id", score.get("song_id"))
        try:
            lxns_id = str(int(raw_id))
        except (TypeError, ValueError):
            lxns_id = str(raw_id or "").strip()

        target_type = self._lxns_song_type(score.get("type"))
        candidates = []
        for song in self.music_data:
            if not isinstance(song, dict):
                continue
            try:
                water_id = int(song["id"])
            except (KeyError, TypeError, ValueError):
                continue
            current_lxns_id = (
                str(water_id)
                if self.is_utage_chart(water_id)
                else self._normalize_song_id_for_lxns(
                    water_id, self._is_post_finale_version(song)
                )
            )
            if current_lxns_id == lxns_id:
                candidates.append(song)

        typed_candidates = [
            song for song in candidates
            if self._lxns_song_type(song.get("type")) == target_type
        ]
        if typed_candidates:
            return typed_candidates[0]
        if candidates:
            return candidates[0]

        # ID 规则发生变动时，以歌名作为最后的兼容匹配方式。
        title = self._normalize_title(score.get("song_name"))
        if title:
            title_candidates = [
                song for song in self.music_data
                if self._normalize_title(song.get("title")) == title
            ]
            typed_candidates = [
                song for song in title_candidates
                if self._lxns_song_type(song.get("type")) == target_type
            ]
            return (typed_candidates or title_candidates or [None])[0]
        return None

    async def _get_lxns_player_records(self, qq: str) -> Optional[Dict[str, Any]]:
        """读取落雪玩家信息和完整成绩，并转换成插件内部统一格式。"""
        if self.lxns_oauth is None:
            raise LxnsOAuthConsentRequired(
                "未初始化落雪 OAuth。", code="not_configured"
            )
        if not self.music_data:
            await self.load_music_data()

        token = await self.lxns_oauth.get_access_token(str(qq))
        headers = {"Authorization": f"Bearer {token}"}

        async def request(endpoint: str):
            url = f"{self.lxns_base_url}{endpoint}"
            response = await self.client.get(url, headers=headers)
            if response.status_code == 401:
                # 落雪 Access Token 过期时只刷新并重试一次。
                token_retry = await self.lxns_oauth.get_access_token(
                    str(qq), force_refresh=True
                )
                response = await self.client.get(
                    url,
                    headers={"Authorization": f"Bearer {token_retry}"},
                )
                headers["Authorization"] = f"Bearer {token_retry}"
            if response.status_code == 429:
                raise LxnsOAuthQuotaExceeded(
                    "落雪 OAuth 请求过于频繁，请稍后再试。",
                    code="rate_limited",
                    status_code=429,
                )
            if response.status_code == 401:
                raise LxnsOAuthConsentRequired(
                    "落雪 OAuth 授权已失效，请重新绑定落雪账号。",
                    code="consent_required",
                    status_code=401,
                )
            if response.status_code < 200 or response.status_code >= 300:
                try:
                    payload = response.json()
                    error_msg = payload.get("message")
                except ValueError:
                    error_msg = None
                logger.warning(
                    f"获取落雪用户 {qq} 成绩失败: "
                    f"{error_msg or f'HTTP {response.status_code}'}"
                )
                return None
            try:
                payload = response.json()
            except ValueError:
                logger.warning(f"落雪用户 {qq} 成绩响应不是有效 JSON")
                return None
            if isinstance(payload, dict) and "data" in payload:
                return payload.get("data")
            return payload

        try:
            player = await request("")
            scores = await request("/scores")
            if not isinstance(player, dict) or not isinstance(scores, list):
                return None

            level_labels = ["Basic", "Advanced", "Expert", "Master", "Re:MASTER"]
            normalized_records = []
            for score in scores:
                if not isinstance(score, dict):
                    continue
                level_index = self._lxns_level_index(score.get("level_index"))
                song = self._find_lxns_song(score)
                raw_song_id = score.get("id", score.get("song_id"))
                try:
                    song_id = int(song["id"]) if song else int(raw_song_id)
                except (KeyError, TypeError, ValueError):
                    continue

                ds = 0
                if song and isinstance(song.get("ds"), list):
                    try:
                        ds = float(song["ds"][level_index] or 0)
                    except (IndexError, TypeError, ValueError):
                        ds = 0

                normalized_records.append(
                    {
                        "song_id": song_id,
                        "level_index": level_index,
                        "level_label": level_labels[level_index],
                        "ds": ds,
                        "achievements": float(score.get("achievements") or 0),
                        "fc": score.get("fc") or "",
                        "fs": score.get("fs") or "",
                        "rate": score.get("rate") or "",
                    }
                )

            return {
                "nickname": player.get("name") or player.get("nickname") or "未知",
                "rating": player.get("rating", 0),
                "records": normalized_records,
                "source": "lxns",
            }
        except OAuthError:
            raise
        except httpx.HTTPError as e:
            logger.error(f"获取落雪用户 {qq} 成绩时网络错误: {e}")
            return None
        except (TypeError, ValueError) as e:
            logger.error(f"获取落雪用户 {qq} 成绩时响应格式错误: {e}")
            return None

    async def _get_divingfish_player_records(self, qq: str) -> Optional[Dict[str, Any]]:
        token = await self.oauth.get_access_token(str(qq))
        url = f"{self.base_url}/player/records"
        headers = {"Authorization": f"Bearer {token}"}

        try:
            response = await self.client.get(url, headers=headers)
            if response.status_code == 401:
                # Access Token 失效时只刷新一次，避免请求风暴。
                await self.oauth.invalidate_access_token(str(qq))
                token = await self.oauth.get_access_token(str(qq), force_refresh=True)
                response = await self.client.get(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                )

            if response.status_code == 200:
                payload = response.json()
                if isinstance(payload, dict):
                    payload = dict(payload)
                    payload["source"] = "divingfish"
                return payload

            if response.status_code == 429:
                raise OAuthQuotaExceeded(
                    "已超出水鱼 OAuth 今日调用上限。",
                    code="quota_exceeded",
                    status_code=429,
                )

            try:
                payload = response.json()
                error_msg = payload.get("message") or payload.get("error_description")
            except ValueError:
                error_msg = None
            if response.status_code in {400, 403}:
                logger.warning(
                    f"获取玩家 {qq} 成绩失败: {error_msg or f'HTTP {response.status_code}'}"
                )
            else:
                logger.error(f"获取玩家 {qq} 成绩失败: HTTP {response.status_code}")
            return None
        except OAuthError:
            raise
        except httpx.HTTPError as e:
            logger.error(f"获取玩家 {qq} 成绩时网络错误: {e}")
            return None
        except (TypeError, ValueError) as e:
            logger.error(f"获取玩家 {qq} 成绩时响应格式错误: {e}")
            return None

    async def get_player_records(
        self,
        qq: str,
        source: str = "divingfish",
    ) -> Optional[Dict[str, Any]]:
        """获取玩家完整成绩，支持水鱼和落雪两种 OAuth 数据源。"""
        if str(source).strip().lower() == "lxns":
            return await self._get_lxns_player_records(qq)
        return await self._get_divingfish_player_records(qq)

    async def search_songs(
        self, query: str, limit: int = 5, *, include_utage: bool = False
    ) -> List[SongSearchResult]:
        """搜索歌曲并返回按匹配质量排序的候选结果。

        普通标题与别名搜索不让宴谱参与相近结果排序；宴谱仍可用精确 ID
        查询，或由排行榜的“原歌 + 宴谱标签”入口解析。
        """
        query = str(query or "").strip()
        if not query:
            return []

        if not self.music_data:
            await self.load_music_data()
        if not self.alias_data:
            await self.load_alias_data()

        query_normalized = self._normalize_search_text(query)
        query_compact = self._normalize_search_text(query, compact=True)
        if not query_normalized or not query_compact:
            return []

        try:
            result_limit = max(1, int(limit))
        except (TypeError, ValueError):
            result_limit = 5

        songs_by_id: Dict[int, dict] = {}
        for song in self.music_data:
            if not isinstance(song, dict):
                continue
            try:
                song_id = int(song["id"])
            except (KeyError, TypeError, ValueError):
                continue
            songs_by_id.setdefault(song_id, song)

        matches: Dict[int, SongSearchResult] = {}

        def add_match(song_id: int, song: dict, score: int, matched_by: str):
            result = SongSearchResult(song=song, score=score, matched_by=matched_by)
            previous = matches.get(song_id)
            if previous is None or result.score > previous.score:
                matches[song_id] = result

        # 数字查询优先精确匹配歌曲 ID，支持全角数字。
        if query_compact.isdigit():
            try:
                song_id = int(query_compact)
            except ValueError:
                song_id = None
            if song_id is not None and song_id in songs_by_id:
                add_match(song_id, songs_by_id[song_id], 2000, "id_exact")

        for song_id, song in songs_by_id.items():
            if not include_utage and is_utage_song(song):
                continue
            title = song.get("title")
            if not isinstance(title, str):
                continue
            scored = self._score_search_term(query, title, "title")
            if scored is not None:
                add_match(song_id, song, scored[0], scored[1])

        # alias_data 已经合并了多个别名源和自定义别名；这里再次按歌曲去重，
        # 避免同一首歌拥有多个相同别名时重复参与排序。
        seen_aliases = set()
        for alias_item in self.alias_data:
            if not isinstance(alias_item, dict):
                continue
            try:
                song_id = int(alias_item.get("SongID"))
            except (TypeError, ValueError):
                continue
            song = songs_by_id.get(song_id)
            if song is None or (not include_utage and is_utage_song(song)):
                continue
            aliases = alias_item.get("Alias")
            if not isinstance(aliases, list):
                continue
            for alias in aliases:
                if not isinstance(alias, str):
                    continue
                alias_key = (song_id, self._normalize_search_text(alias, compact=True))
                if alias_key in seen_aliases:
                    continue
                seen_aliases.add(alias_key)
                scored = self._score_search_term(query, alias, "alias")
                if scored is not None:
                    add_match(song_id, song, scored[0], scored[1])

        results = sorted(
            matches.values(),
            key=lambda result: (
                -result.score,
                self._normalize_search_text(result.song.get("title")),
                str(result.song.get("id", "")),
            ),
        )
        return results[:result_limit]

    def get_utage_markers(self) -> set[str]:
        """返回曲库中所有宴谱方括号标签的规范化值。"""
        markers = set()
        for song in self.music_data:
            if not isinstance(song, dict) or not is_utage_song(song):
                continue
            title_parts = split_utage_title(song.get("title"))
            if title_parts is not None:
                markers.add(self._normalize_search_text(title_parts[0], compact=True))
        return markers

    def get_utage_variants(
        self, base_song: dict, marker: Optional[str] = None
    ) -> List[SongSearchResult]:
        """按原歌和可选方括号标签查找对应宴谱。"""
        base_title = self._normalize_search_text(base_song.get("title"))
        base_compact = self._normalize_search_text(
            base_song.get("title"), compact=True
        )
        marker_compact = (
            self._normalize_search_text(marker, compact=True) if marker else ""
        )
        if not base_title or not base_compact:
            return []

        matches = []
        for song in self.music_data:
            if not isinstance(song, dict) or not is_utage_song(song):
                continue
            title_parts = split_utage_title(song.get("title"))
            if title_parts is None:
                continue
            candidate_marker, candidate_title = title_parts
            if marker_compact and self._normalize_search_text(
                candidate_marker, compact=True
            ) != marker_compact:
                continue

            candidate_normalized = self._normalize_search_text(candidate_title)
            candidate_compact = self._normalize_search_text(
                candidate_title, compact=True
            )
            exact = candidate_compact == base_compact
            parenthesized_variant = (
                candidate_normalized.startswith(base_title)
                and candidate_normalized[len(base_title) :]
                .lstrip()
                .startswith(("(", "["))
            )
            if not exact and not parenthesized_variant:
                continue
            matches.append(
                SongSearchResult(
                    song=song,
                    score=1100 if exact else 1050,
                    matched_by="utage_base_title",
                )
            )

        return sorted(
            matches,
            key=lambda result: (
                -result.score,
                self._normalize_search_text(result.song.get("title")),
                str(result.song.get("id", "")),
            ),
        )

    @staticmethod
    def is_ambiguous_song_search(results: List[SongSearchResult]) -> bool:
        """判断搜索结果是否需要用户进一步缩小范围。"""
        if len(results) < 2:
            return False
        top, second = results[0], results[1]
        # ID、歌曲名或别名的规范化精确匹配可以直接使用。
        if top.score >= 940:
            return False
        # 低置信度结果不应静默选中；接近的前缀/包含结果也需要提示候选。
        if top.score < 500:
            return True
        return second.score >= top.score - 20

    async def find_song(self, query: str) -> Optional[dict]:
        """查找最佳匹配歌曲，兼容现有调用方。"""
        results = await self.search_songs(query, limit=1)
        return results[0].song if results else None

    def _convert_song_id_to_cover_id(self, song_id: int) -> int:
        """根据规则转换歌曲ID为封面ID

        Args:
            song_id: 原始歌曲ID

        Returns:
            转换后的封面ID
        """
        song_id_str = str(song_id)

        if len(song_id_str) == 6 and song_id_str.startswith("100"):
            cover_id_str = song_id_str[3:].lstrip("0")
            return int(cover_id_str) if cover_id_str else 0
        elif len(song_id_str) == 5 and song_id_str.startswith("10"):
            cover_id_str = song_id_str[2:].lstrip("0")
            return int(cover_id_str) if cover_id_str else 0
        elif len(song_id_str) >= 5:
            cover_id_str = song_id_str[1:].lstrip("0")
            return int(cover_id_str) if cover_id_str else 0
        else:
            return song_id

    async def get_song_cover(self, song_id: int) -> Optional[bytes]:
        """获取歌曲封面（带数据库缓存）

        Args:
            song_id: 歌曲 ID

        Returns:
            封面图片字节数据，失败返回 None
        """
        try:
            if self.is_utage_chart(song_id):
                matched_cover_id = None
                if self.music_data:
                    song_info = None
                    for song in self.music_data:
                        if int(song.get("id")) == song_id:
                            song_info = song
                            break
                    if song_info and "title" in song_info:
                        title = song_info["title"]
                        if len(title) > 3:
                            search_title = title[3:].lower()
                            for song in self.music_data:
                                if not self.is_utage_chart(int(song.get("id"))):
                                    if song.get("title", "").lower() == search_title:
                                        matched_cover_id = self._convert_song_id_to_cover_id(int(song.get("id")))
                                        break
                if matched_cover_id is not None:
                    cover_id = matched_cover_id
                else:
                    cover_id = self._convert_song_id_to_cover_id(song_id)
            else:
                cover_id = self._convert_song_id_to_cover_id(song_id)

            cover_id_str = str(cover_id)

            # 检查数据库缓存
            try:
                async with aiosqlite.connect(self.cache_db_file) as db:
                    db.row_factory = sqlite3.Row
                    cursor = await db.execute(
                        "SELECT cover_data FROM cover_cache WHERE song_id = ?",
                        (song_id,)
                    )
                    row = await cursor.fetchone()
                    if row:
                        return row["cover_data"]
            except Exception as e:
                logger.warning(f"读取封面缓存失败: {e}")

            # 从网络获取
            base_url = "https://assets2.lxns.net/maimai"
            url = f"{base_url}/jacket/{cover_id_str}.png"

            logger.debug(f"正在获取封面: song_id={song_id}, cover_id={cover_id}, URL={url}")
            response = await self.client.get(url)

            if response.status_code == 200:
                cover_data = response.content

                # 保存到数据库缓存
                try:
                    async with aiosqlite.connect(self.cache_db_file) as db:
                        db.row_factory = sqlite3.Row
                        from datetime import datetime
                        cached_at = datetime.now().isoformat()
                        await db.execute(
                            "INSERT OR REPLACE INTO cover_cache (song_id, cover_data, cached_at) VALUES (?, ?, ?)",
                            (song_id, cover_data, cached_at)
                        )
                        await db.commit()
                        logger.debug(f"封面已缓存到数据库: song_id={song_id}, cover_id={cover_id}")
                except Exception as e:
                    logger.warning(f"保存封面缓存到数据库失败: {e}")

                return cover_data
            else:
                logger.warning(f"获取歌曲 {song_id} 封面失败: HTTP {response.status_code}, URL={url}")
                return None

        except Exception as e:
            logger.error(f"获取歌曲 {song_id} 封面时出错: {e}")
            return None

    async def get_cover_thumbnail(self, song_id: int) -> Optional[bytes]:
        """从数据库获取已处理好的缩略图（197×197 圆角 PNG）"""
        try:
            async with aiosqlite.connect(self.cache_db_file) as db:
                db.row_factory = sqlite3.Row
                cursor = await db.execute(
                    "SELECT thumbnail FROM cover_thumbnail WHERE song_id = ?",
                    (song_id,)
                )
                row = await cursor.fetchone()
                return row["thumbnail"] if row else None
        except Exception as e:
            logger.warning(f"读取缩略图缓存失败: {e}")
            return None

    async def save_cover_thumbnail(self, song_id: int, thumbnail_data: bytes):
        """保存处理好的缩略图到数据库"""
        try:
            async with aiosqlite.connect(self.cache_db_file) as db:
                db.row_factory = sqlite3.Row
                from datetime import datetime
                cached_at = datetime.now().isoformat()
                await db.execute(
                    "INSERT OR REPLACE INTO cover_thumbnail (song_id, thumbnail, cached_at) VALUES (?, ?, ?)",
                    (song_id, thumbnail_data, cached_at)
                )
                await db.commit()
        except Exception as e:
            logger.warning(f"保存缩略图缓存失败: {e}")

    async def clear_cover_cache(self) -> int:
        """清除所有歌曲封面缓存（原始封面 + 缩略图）

        Returns:
            清除的缓存记录数量
        """
        try:
            async with aiosqlite.connect(self.cache_db_file) as db:
                db.row_factory = sqlite3.Row
                cursor = await db.execute("DELETE FROM cover_cache")
                count = cursor.rowcount
                await db.execute("DELETE FROM cover_thumbnail")
                await db.commit()

            logger.info(f"已清除 {count} 条封面缓存（含缩略图）")
            return count
        except Exception as e:
            logger.error(f"清除封面缓存失败: {e}")
            return 0

    async def get_help_image(self, is_admin: bool = False) -> Optional[bytes]:
        """从数据库获取预渲染的帮助图片"""
        try:
            async with aiosqlite.connect(self.cache_db_file) as db:
                db.row_factory = sqlite3.Row
                cursor = await db.execute(
                    "SELECT data FROM help_image_cache WHERE is_admin = ?", (1 if is_admin else 0,)
                )
                row = await cursor.fetchone()
                return row["data"] if row else None
        except Exception as e:
            logger.warning(f"读取帮助图片缓存失败: {e}")
            return None

    async def save_help_image(self, is_admin: bool, data: bytes):
        """保存预渲染的帮助图片到数据库"""
        try:
            async with aiosqlite.connect(self.cache_db_file) as db:
                db.row_factory = sqlite3.Row
                from datetime import datetime
                cached_at = datetime.now().isoformat()
                await db.execute(
                    "INSERT OR REPLACE INTO help_image_cache (is_admin, data, cached_at) VALUES (?, ?, ?)",
                    (1 if is_admin else 0, data, cached_at)
                )
                await db.commit()
        except Exception as e:
            logger.warning(f"保存帮助图片缓存失败: {e}")

    async def close(self):
        """关闭 HTTP 客户端"""
        await self.client.aclose()
