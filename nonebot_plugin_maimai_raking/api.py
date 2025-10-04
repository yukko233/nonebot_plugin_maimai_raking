"""API 模块 - 对接水鱼 API 和别名 API"""
import httpx
import json
import sqlite3
from pathlib import Path
from typing import Optional, Dict, List, Any
from nonebot.log import logger


class MaimaiAPI:
    """舞萌 API 客户端"""
    
    def __init__(self, developer_token: str):
        """初始化 API 客户端
        
        Args:
            developer_token: 水鱼查分器 Developer Token
        """
        self.developer_token = developer_token
        self.base_url = "https://www.diving-fish.com/api/maimaidxprober"
        self.alias_url = "https://www.yuzuchan.moe/api/maimaidx/maimaidxalias"
        
        # 缓存数据
        self.music_data: List[dict] = []
        self.alias_data: List[dict] = []
        
        # 本地缓存数据库路径
        self.cache_dir = Path("data/maimai_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_db_file = self.cache_dir / "cache.db"
        
        # 初始化缓存数据库
        self._init_cache_database()
        
        # HTTP 客户端
        self.client = httpx.AsyncClient(timeout=30.0)
    
    def _get_cache_connection(self) -> sqlite3.Connection:
        """获取缓存数据库连接"""
        conn = sqlite3.connect(self.cache_db_file)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _init_cache_database(self):
        """初始化缓存数据库表结构"""
        conn = self._get_cache_connection()
        cursor = conn.cursor()
        
        try:
            # 创建别名数据缓存表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS alias_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    data TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            
            # 创建封面缓存表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cover_cache (
                    song_id INTEGER PRIMARY KEY,
                    cover_data BLOB NOT NULL,
                    cached_at TEXT NOT NULL
                )
            """)
            
            conn.commit()
            logger.info("API 缓存数据库初始化完成")
        except Exception as e:
            logger.error(f"初始化缓存数据库失败: {e}")
            conn.rollback()
        finally:
            conn.close()
    
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
    
    async def load_alias_data(self):
        """加载别名数据（优先从数据库缓存加载）"""
        # 1. 尝试从数据库缓存加载
        conn = self._get_cache_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("SELECT data FROM alias_cache ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
            
            if row:
                self.alias_data = json.loads(row["data"])
                logger.info(f"从数据库缓存加载 {len(self.alias_data)} 条别名数据")
                conn.close()
                return
        except Exception as e:
            logger.warning(f"加载数据库别名缓存失败: {e}，将从API获取")
        finally:
            conn.close()
        
        # 2. 从API加载
        try:
            response = await self.client.get(self.alias_url)
            
            if response.status_code == 200:
                data = response.json()
                
                # 处理不同的数据格式
                if isinstance(data, list):
                    self.alias_data = data
                elif isinstance(data, dict):
                    # 如果是字典，尝试获取content字段
                    if "content" in data:
                        self.alias_data = data["content"]
                    else:
                        # 将字典的values转为列表
                        self.alias_data = list(data.values()) if data else []
                else:
                    logger.warning(f"别名数据格式不正确: {type(data)}")
                    self.alias_data = []
                
                # 保存到数据库缓存
                if self.alias_data:
                    try:
                        conn = self._get_cache_connection()
                        cursor = conn.cursor()
                        
                        from datetime import datetime
                        data_json = json.dumps(self.alias_data, ensure_ascii=False)
                        updated_at = datetime.now().isoformat()
                        
                        cursor.execute(
                            "INSERT INTO alias_cache (data, updated_at) VALUES (?, ?)",
                            (data_json, updated_at)
                        )
                        
                        conn.commit()
                        conn.close()
                        logger.info(f"成功加载并缓存 {len(self.alias_data)} 条别名数据到数据库")
                    except Exception as e:
                        logger.error(f"保存别名缓存到数据库失败: {e}")
                        logger.info(f"成功加载 {len(self.alias_data)} 条别名数据（未缓存）")
            else:
                logger.error(f"加载别名数据失败: {response.status_code}")
                self.alias_data = []
        except Exception as e:
            logger.error(f"加载别名数据时出错: {e}")
            self.alias_data = []
    
    async def load_alias_data_force(self):
        """强制从网络重新加载别名数据（用于定时更新）"""
        try:
            logger.info("正在从网络强制更新别名数据...")
            response = await self.client.get(self.alias_url)
            
            if response.status_code == 200:
                data = response.json()
                
                # 处理不同的数据格式
                if isinstance(data, list):
                    self.alias_data = data
                elif isinstance(data, dict):
                    # 如果是字典，尝试获取content字段
                    if "content" in data:
                        self.alias_data = data["content"]
                    else:
                        # 将字典的values转为列表
                        self.alias_data = list(data.values()) if data else []
                else:
                    logger.warning(f"别名数据格式不正确: {type(data)}")
                    self.alias_data = []
                
                # 保存到数据库缓存
                if self.alias_data:
                    try:
                        conn = self._get_cache_connection()
                        cursor = conn.cursor()
                        
                        from datetime import datetime
                        data_json = json.dumps(self.alias_data, ensure_ascii=False)
                        updated_at = datetime.now().isoformat()
                        
                        cursor.execute(
                            "INSERT INTO alias_cache (data, updated_at) VALUES (?, ?)",
                            (data_json, updated_at)
                        )
                        
                        conn.commit()
                        conn.close()
                        logger.info(f"强制更新并缓存 {len(self.alias_data)} 条别名数据到数据库")
                    except Exception as e:
                        logger.error(f"保存别名缓存到数据库失败: {e}")
                        logger.info(f"强制更新 {len(self.alias_data)} 条别名数据（未缓存）")
            else:
                logger.error(f"强制更新别名数据失败: {response.status_code}")
        except Exception as e:
            logger.error(f"强制更新别名数据时出错: {e}")
    
    async def get_player_records(self, qq: str) -> Optional[Dict[str, Any]]:
        """获取玩家完整成绩
        
        Args:
            qq: 玩家 QQ 号
            
        Returns:
            玩家成绩数据，失败返回 None
        """
        try:
            url = f"{self.base_url}/dev/player/records"
            headers = {"Developer-Token": self.developer_token}
            params = {"qq": qq}
            
            response = await self.client.get(url, headers=headers, params=params)
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 400:
                error_msg = response.json().get("message", "未知错误")
                logger.warning(f"获取玩家 {qq} 成绩失败: {error_msg}")
                return None
            else:
                logger.error(f"获取玩家 {qq} 成绩失败: HTTP {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"获取玩家 {qq} 成绩时出错: {e}")
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
        
        # 如果歌曲数据未加载，先加载
        if not self.music_data:
            await self.load_music_data()
        
        if not self.alias_data:
            await self.load_alias_data()
        
        # 1. 尝试按 ID 查找
        if query.isdigit():
            song_id = int(query)
            for song in self.music_data:
                try:
                    # 确保song["id"]是整数类型
                    current_song_id = int(song["id"])
                    if current_song_id == song_id:
                        # 排除宴谱
                        if self.is_utage_chart(current_song_id):
                            continue
                        return song
                except (ValueError, TypeError):
                    continue
        
        # 2. 尝试按歌曲名精确匹配
        for song in self.music_data:
            if song["title"].lower() == query.lower():
                try:
                    # 排除宴谱
                    if self.is_utage_chart(int(song["id"])):
                        continue
                    return song
                except (ValueError, TypeError):
                    continue
        
        # 3. 尝试按别名查找
        for alias_item in self.alias_data:
            # 检查别名列表
            if "Alias" in alias_item and isinstance(alias_item["Alias"], list):
                for alias in alias_item["Alias"]:
                    if alias.lower() == query.lower():
                        # 找到匹配的别名，返回对应歌曲
                        song_id = alias_item.get("SongID")
                        if song_id is not None:
                            # 确保类型一致（转为整数）
                            try:
                                song_id = int(song_id)
                            except (ValueError, TypeError):
                                continue
                            
                            for song in self.music_data:
                                # 确保 song["id"] 也是整数
                                try:
                                    current_song_id = int(song["id"])
                                    if current_song_id == song_id:
                                        # 排除宴谱
                                        if self.is_utage_chart(current_song_id):
                                            continue
                                        return song
                                except (ValueError, TypeError):
                                    continue
        
        # 4. 收集所有模糊匹配结果并按匹配度排序
        matches = []
        
        # 4.1 按歌曲名模糊匹配
        for song in self.music_data:
            title = song["title"].lower()
            query_lower = query.lower()
            if query_lower in title:
                try:
                    # 排除宴谱
                    if self.is_utage_chart(int(song["id"])):
                        continue
                    # 计算匹配度：完全匹配 > 开头匹配 > 包含匹配
                    if title == query_lower:
                        score = 100  # 完全匹配
                    elif title.startswith(query_lower):
                        score = 90   # 开头匹配
                    else:
                        score = 80   # 包含匹配
                    matches.append((score, song, "title"))
                except (ValueError, TypeError):
                    continue
        
        # 4.2 按别名模糊匹配
        for alias_item in self.alias_data:
            if "Alias" in alias_item and isinstance(alias_item["Alias"], list):
                for alias in alias_item["Alias"]:
                    alias_lower = alias.lower()
                    query_lower = query.lower()
                    
                    # 更精确的匹配逻辑
                    match_score = 0
                    
                    # 创建去空格版本用于比较
                    alias_no_space = alias_lower.replace(" ", "").replace("-", "").replace("_", "")
                    query_no_space = query_lower.replace(" ", "").replace("-", "").replace("_", "")
                    
                    if alias_lower == query_lower:
                        match_score = 95   # 别名完全匹配
                    elif alias_no_space == query_no_space and len(query_no_space) >= 3:
                        match_score = 93   # 去空格后完全匹配
                    elif alias_lower.startswith(query_lower):
                        match_score = 85   # 别名开头匹配
                    elif alias_no_space.startswith(query_no_space) and len(query_no_space) >= 3:
                        match_score = 83   # 去空格后开头匹配
                    elif query_lower.startswith(alias_lower):
                        # 查询词以别名开头，但要求别名长度合理且不能太短
                        if len(alias_lower) >= 5 and len(alias_lower) / len(query_lower) >= 0.6:
                            match_score = 82
                    elif query_no_space.startswith(alias_no_space) and len(alias_no_space) >= 4:
                        # 去空格后查询词以别名开头
                        if len(alias_no_space) / len(query_no_space) >= 0.5:
                            match_score = 80
                    elif alias_lower in query_lower:
                        # 别名包含在查询词中，但要求别名长度合理且不能太短
                        if len(alias_lower) >= 5 and len(alias_lower) / len(query_lower) >= 0.5:
                            match_score = 78
                    elif alias_no_space in query_no_space and len(alias_no_space) >= 4:
                        # 去空格后别名包含在查询词中
                        if len(alias_no_space) / len(query_no_space) >= 0.4:
                            match_score = 76
                    elif query_lower in alias_lower:
                        # 查询词包含在别名中，要求查询词不能太短
                        if len(query_lower) >= 4:
                            match_score = 75
                    elif query_no_space in alias_no_space and len(query_no_space) >= 3:
                        # 去空格后查询词包含在别名中
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
                                        # 排除宴谱
                                        if self.is_utage_chart(current_song_id):
                                            continue
                                        matches.append((match_score, song, "alias"))
                                        break
                                except (ValueError, TypeError):
                                    continue
        
        # 按匹配度排序，返回最佳匹配
        if matches:
            matches.sort(key=lambda x: x[0], reverse=True)
            return matches[0][1]
        
        return None
    
    async def get_song_cover(self, song_id: int) -> Optional[bytes]:
        """获取歌曲封面（带数据库缓存）
        
        Args:
            song_id: 歌曲 ID
            
        Returns:
            封面图片字节数据，失败返回 None
        """
        try:
            # 处理 ID 格式
            cover_id = song_id
            if 10000 < song_id <= 11000:
                cover_id = song_id - 10000
            
            # 补齐为 5 位数
            cover_id_str = f"{cover_id:05d}"
            
            # 检查数据库缓存
            conn = self._get_cache_connection()
            cursor = conn.cursor()
            
            try:
                cursor.execute(
                    "SELECT cover_data FROM cover_cache WHERE song_id = ?",
                    (cover_id,)
                )
                row = cursor.fetchone()
                
                if row:
                    conn.close()
                    return row["cover_data"]
            except Exception as e:
                logger.warning(f"读取封面缓存失败: {e}")
            finally:
                conn.close()
            
            # 从网络获取
            url = f"https://www.diving-fish.com/covers/{cover_id_str}.png"
            response = await self.client.get(url)
            
            if response.status_code == 200:
                cover_data = response.content
                
                # 保存到数据库缓存
                try:
                    conn = self._get_cache_connection()
                    cursor = conn.cursor()
                    
                    from datetime import datetime
                    cached_at = datetime.now().isoformat()
                    
                    cursor.execute(
                        "INSERT OR REPLACE INTO cover_cache (song_id, cover_data, cached_at) VALUES (?, ?, ?)",
                        (cover_id, cover_data, cached_at)
                    )
                    
                    conn.commit()
                    conn.close()
                    logger.debug(f"封面已缓存到数据库: song_id={cover_id}")
                except Exception as e:
                    logger.warning(f"保存封面缓存到数据库失败: {e}")
                
                return cover_data
            else:
                logger.warning(f"获取歌曲 {song_id} 封面失败: HTTP {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"获取歌曲 {song_id} 封面时出错: {e}")
            return None
    
    async def close(self):
        """关闭 HTTP 客户端"""
        await self.client.aclose()

