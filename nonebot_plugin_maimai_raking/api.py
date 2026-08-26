"""API 模块 - 对接水鱼 API 和别名 API"""
import asyncio
import aiosqlite
import httpx
import json
import sqlite3
import unicodedata
from pathlib import Path
from typing import Optional, Dict, List, Any
from nonebot.log import logger

from .oauth import OAuthError, OAuthManager, OAuthQuotaExceeded


class MaimaiAPI:
    """舞萌 API 客户端"""

    def __init__(self, oauth: OAuthManager):
        """初始化 API 客户端

        Args:
            oauth: 水鱼 OAuth 管理器
        """
        import nonebot_plugin_localstore as store

        self.oauth = oauth
        self.base_url = "https://www.diving-fish.com/api/maimaidxprober"
        self.alias_url = "https://www.yuzuchan.moe/api/maimaidx/maimaidxalias"
        self.alias_lxns_url = "https://maimai.lxns.net/api/v0/maimai/alias/list"
        self.alias_dxrating_url = "https://miruku.dxrating.net/api/v1/aliases"
        self.alias_cache_version = 2

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
        return "".join(char for char in normalized if not char.isspace())

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

    def _merge_yuzu_aliases(self, data: Any, alias_map: Dict[int, List[str]]) -> int:
        count = 0
        for item in self._as_alias_entries(data):
            song_id = item.get("SongID", item.get("song_id"))
            aliases = item.get("Alias", item.get("aliases"))
            count += self._add_aliases(alias_map, song_id, aliases)
        return count

    def _merge_lxns_aliases(self, data: Any, alias_map: Dict[int, List[str]]) -> int:
        count = 0
        for item in self._as_alias_entries(data):
            count += self._add_aliases(
                alias_map, item.get("song_id"), item.get("aliases")
            )
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

    async def get_player_records(self, qq: str) -> Optional[Dict[str, Any]]:
        """获取玩家完整成绩

        Args:
            qq: 玩家 QQ 号

        Returns:
            玩家成绩数据，失败返回 None
        """
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
                return response.json()

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

    async def find_song(self, query: str) -> Optional[dict]:
        """查找歌曲

        支持歌曲 ID、歌曲名、别名查询

        Args:
            query: 查询关键词（ID/歌曲名/别名）

        Returns:
            歌曲信息，未找到返回 None
        """
        query = query.strip()

        if not self.music_data:
            await self.load_music_data()
        if not self.alias_data:
            await self.load_alias_data()

        # 1. 尝试按 ID 查找
        if query.isdigit():
            song_id = int(query)
            for song in self.music_data:
                try:
                    current_song_id = int(song["id"])
                    if current_song_id == song_id:
                        if self.is_utage_chart(current_song_id):
                            continue
                        return song
                except (ValueError, TypeError):
                    continue

        # 2. 尝试按歌曲名精确匹配
        for song in self.music_data:
            if song["title"].lower() == query.lower():
                try:
                    if self.is_utage_chart(int(song["id"])):
                        continue
                    return song
                except (ValueError, TypeError):
                    continue

        # 3. 尝试按别名查找
        for alias_item in self.alias_data:
            if "Alias" in alias_item and isinstance(alias_item["Alias"], list):
                for alias in alias_item["Alias"]:
                    if alias.lower() == query.lower():
                        song_id = alias_item.get("SongID")
                        if song_id is not None:
                            try:
                                song_id = int(song_id)
                            except (ValueError, TypeError):
                                continue
                            for song in self.music_data:
                                try:
                                    current_song_id = int(song["id"])
                                    if current_song_id == song_id:
                                        if self.is_utage_chart(current_song_id):
                                            continue
                                        return song
                                except (ValueError, TypeError):
                                    continue

        # 4. 模糊匹配
        matches = []

        # 4.1 按歌曲名模糊匹配
        for song in self.music_data:
            title = song["title"].lower()
            query_lower = query.lower()
            if query_lower in title:
                try:
                    if self.is_utage_chart(int(song["id"])):
                        continue
                    if title == query_lower:
                        score = 100
                    elif title.startswith(query_lower):
                        score = 90
                    else:
                        score = 80
                    matches.append((score, song, "title"))
                except (ValueError, TypeError):
                    continue

        # 4.2 按别名模糊匹配
        for alias_item in self.alias_data:
            if "Alias" in alias_item and isinstance(alias_item["Alias"], list):
                for alias in alias_item["Alias"]:
                    alias_lower = alias.lower()
                    query_lower = query.lower()
                    match_score = 0

                    alias_no_space = alias_lower.replace(" ", "").replace("-", "").replace("_", "")
                    query_no_space = query_lower.replace(" ", "").replace("-", "").replace("_", "")

                    if alias_lower == query_lower:
                        match_score = 95
                    elif alias_no_space == query_no_space and len(query_no_space) >= 3:
                        match_score = 93
                    elif alias_lower.startswith(query_lower):
                        match_score = 85
                    elif alias_no_space.startswith(query_no_space) and len(query_no_space) >= 3:
                        match_score = 83
                    elif query_lower.startswith(alias_lower):
                        if len(alias_lower) >= 5 and len(alias_lower) / len(query_lower) >= 0.6:
                            match_score = 82
                    elif query_no_space.startswith(alias_no_space) and len(alias_no_space) >= 4:
                        if len(alias_no_space) / len(query_no_space) >= 0.5:
                            match_score = 80
                    elif alias_lower in query_lower:
                        if len(alias_lower) >= 5 and len(alias_lower) / len(query_lower) >= 0.5:
                            match_score = 78
                    elif alias_no_space in query_no_space and len(alias_no_space) >= 4:
                        if len(alias_no_space) / len(query_no_space) >= 0.4:
                            match_score = 76
                    elif query_lower in alias_lower:
                        if len(query_lower) >= 4:
                            match_score = 75
                    elif query_no_space in alias_no_space and len(query_no_space) >= 3:
                        match_score = 73

                    if match_score > 0:
                        song_id = alias_item.get("SongID")
                        if song_id is not None:
                            try:
                                song_id = int(song_id)
                            except (ValueError, TypeError):
                                continue
                            for song in self.music_data:
                                try:
                                    current_song_id = int(song["id"])
                                    if current_song_id == song_id:
                                        if self.is_utage_chart(current_song_id):
                                            continue
                                        matches.append((match_score, song, "alias"))
                                        break
                                except (ValueError, TypeError):
                                    continue

        if matches:
            matches.sort(key=lambda x: x[0], reverse=True)
            return matches[0][1]

        return None

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
