"""数据库模块 - 使用 SQLite 数据库存储数据"""
import sqlite3
import json
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime
from nonebot.log import logger


class Database:
    """数据库管理类"""
    
    def __init__(self, data_path: Path):
        """初始化数据库
        
        Args:
            data_path: 数据存储路径
        """
        self.data_path = Path(data_path)
        self.data_path.mkdir(parents=True, exist_ok=True)
        
        # 数据库文件路径
        self.db_file = self.data_path / "maimai_raking.db"
        
        # 初始化数据库
        self._init_database()
    
    def _get_connection(self) -> sqlite3.Connection:
        """获取数据库连接"""
        conn = sqlite3.connect(self.db_file)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _init_database(self):
        """初始化数据库表结构"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            # 创建群组表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS groups (
                    group_id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                )
            """)
            
            # 创建用户表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    qq TEXT PRIMARY KEY,
                    joined_at TEXT NOT NULL
                )
            """)
            
            # 创建用户-群组关系表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_groups (
                    qq TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    PRIMARY KEY (qq, group_id),
                    FOREIGN KEY (qq) REFERENCES users(qq),
                    FOREIGN KEY (group_id) REFERENCES groups(group_id)
                )
            """)
            
            # 创建成绩记录表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS records (
                    qq TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (qq) REFERENCES users(qq)
                )
            """)
            
            # 创建刷新记录表（用于频率限制）
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS refresh_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    qq TEXT NOT NULL,
                    refresh_date TEXT NOT NULL,
                    refresh_time TEXT NOT NULL,
                    FOREIGN KEY (qq) REFERENCES users(qq)
                )
            """)
            
            # 创建索引
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_groups_group_id 
                ON user_groups(group_id)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_groups_qq 
                ON user_groups(qq)
            """)
            
            conn.commit()
            logger.info("数据库初始化完成")
        except Exception as e:
            logger.error(f"初始化数据库失败: {e}")
            conn.rollback()
        finally:
            conn.close()
    
    # ==================== 群组管理 ====================
    
    def enable_group(self, group_id: str):
        """开启群组功能"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            # 检查群组是否存在
            cursor.execute("SELECT group_id FROM groups WHERE group_id = ?", (group_id,))
            if cursor.fetchone():
                # 更新为启用
                cursor.execute(
                    "UPDATE groups SET enabled = 1 WHERE group_id = ?",
                    (group_id,)
                )
            else:
                # 插入新群组
                cursor.execute(
                    "INSERT INTO groups (group_id, enabled, created_at) VALUES (?, 1, ?)",
                    (group_id, datetime.now().isoformat())
                )
            
            conn.commit()
            logger.info(f"群组 {group_id} 已开启舞萌排行榜功能")
        except Exception as e:
            logger.error(f"开启群组 {group_id} 失败: {e}")
            conn.rollback()
        finally:
            conn.close()
    
    def disable_group(self, group_id: str):
        """关闭群组功能"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                "UPDATE groups SET enabled = 0 WHERE group_id = ?",
                (group_id,)
            )
            conn.commit()
            logger.info(f"群组 {group_id} 已关闭舞萌排行榜功能")
        except Exception as e:
            logger.error(f"关闭群组 {group_id} 失败: {e}")
            conn.rollback()
        finally:
            conn.close()
    
    def is_group_enabled(self, group_id: str) -> bool:
        """检查群组是否开启功能"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                "SELECT enabled FROM groups WHERE group_id = ?",
                (group_id,)
            )
            row = cursor.fetchone()
            return bool(row["enabled"]) if row else False
        except Exception as e:
            logger.error(f"检查群组 {group_id} 状态失败: {e}")
            return False
        finally:
            conn.close()
    
    # ==================== 用户管理 ====================
    
    def add_user_to_group(self, qq: str, group_id: str):
        """添加用户到群组"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            # 确保群组存在
            cursor.execute("SELECT group_id FROM groups WHERE group_id = ?", (group_id,))
            if not cursor.fetchone():
                cursor.execute(
                    "INSERT INTO groups (group_id, enabled, created_at) VALUES (?, 1, ?)",
                    (group_id, datetime.now().isoformat())
                )
            
            # 确保用户存在
            cursor.execute("SELECT qq FROM users WHERE qq = ?", (qq,))
            if not cursor.fetchone():
                cursor.execute(
                    "INSERT INTO users (qq, joined_at) VALUES (?, ?)",
                    (qq, datetime.now().isoformat())
                )
            
            # 添加用户-群组关系（使用 INSERT OR IGNORE 避免重复）
            cursor.execute(
                "INSERT OR IGNORE INTO user_groups (qq, group_id) VALUES (?, ?)",
                (qq, group_id)
            )
            
            conn.commit()
            logger.info(f"用户 {qq} 已加入群组 {group_id} 的排行榜")
        except Exception as e:
            logger.error(f"添加用户 {qq} 到群组 {group_id} 失败: {e}")
            conn.rollback()
        finally:
            conn.close()
    
    def remove_user_from_group(self, qq: str, group_id: str):
        """从群组移除用户"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                "DELETE FROM user_groups WHERE qq = ? AND group_id = ?",
                (qq, group_id)
            )
            conn.commit()
            logger.info(f"用户 {qq} 已从群组 {group_id} 的排行榜中退出")
        except Exception as e:
            logger.error(f"从群组 {group_id} 移除用户 {qq} 失败: {e}")
            conn.rollback()
        finally:
            conn.close()
    
    def is_user_in_group(self, qq: str, group_id: str) -> bool:
        """检查用户是否在群组中"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                "SELECT 1 FROM user_groups WHERE qq = ? AND group_id = ?",
                (qq, group_id)
            )
            return cursor.fetchone() is not None
        except Exception as e:
            logger.error(f"检查用户 {qq} 是否在群组 {group_id} 失败: {e}")
            return False
        finally:
            conn.close()
    
    def get_group_users(self, group_id: str) -> List[str]:
        """获取群组的所有用户"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                "SELECT qq FROM user_groups WHERE group_id = ?",
                (group_id,)
            )
            return [row["qq"] for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"获取群组 {group_id} 的用户列表失败: {e}")
            return []
        finally:
            conn.close()
    
    def get_all_users(self) -> List[str]:
        """获取所有用户"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("SELECT qq FROM users")
            return [row["qq"] for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"获取所有用户列表失败: {e}")
            return []
        finally:
            conn.close()
    
    def get_all_enabled_groups(self) -> List[str]:
        """获取所有启用的群组"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("SELECT group_id FROM groups WHERE enabled = 1")
            return [row["group_id"] for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"获取启用的群组列表失败: {e}")
            return []
        finally:
            conn.close()
    
    # ==================== 成绩管理 ====================
    
    def update_user_records(self, qq: str, records: dict):
        """更新用户成绩"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            # 将 records 转换为 JSON 字符串存储
            data_json = json.dumps(records, ensure_ascii=False)
            updated_at = datetime.now().isoformat()
            
            # 使用 INSERT OR REPLACE 来更新或插入
            cursor.execute(
                "INSERT OR REPLACE INTO records (qq, data, updated_at) VALUES (?, ?, ?)",
                (qq, data_json, updated_at)
            )
            
            conn.commit()
            logger.info(f"用户 {qq} 的成绩已更新")
        except Exception as e:
            logger.error(f"更新用户 {qq} 的成绩失败: {e}")
            conn.rollback()
        finally:
            conn.close()
    
    def get_user_records(self, qq: str) -> Optional[dict]:
        """获取用户成绩"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                "SELECT data FROM records WHERE qq = ?",
                (qq,)
            )
            row = cursor.fetchone()
            if row:
                return json.loads(row["data"])
            return None
        except Exception as e:
            logger.error(f"获取用户 {qq} 的成绩失败: {e}")
            return None
        finally:
            conn.close()
    
    def get_last_update_time(self, qq: str) -> Optional[str]:
        """获取用户成绩的最后更新时间"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                "SELECT updated_at FROM records WHERE qq = ?",
                (qq,)
            )
            row = cursor.fetchone()
            return row["updated_at"] if row else None
        except Exception as e:
            logger.error(f"获取用户 {qq} 的更新时间失败: {e}")
            return None
        finally:
            conn.close()
    
    def get_daily_refresh_count(self, qq: str, date: str) -> int:
        """获取用户指定日期的刷新次数"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                "SELECT COUNT(*) as count FROM refresh_logs WHERE qq = ? AND refresh_date = ?",
                (qq, date)
            )
            row = cursor.fetchone()
            return row["count"] if row else 0
        except Exception as e:
            logger.error(f"获取用户 {qq} 的刷新次数失败: {e}")
            return 0
        finally:
            conn.close()
    
    def log_refresh(self, qq: str, date: str):
        """记录用户刷新操作"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            refresh_time = datetime.now().isoformat()
            cursor.execute(
                "INSERT INTO refresh_logs (qq, refresh_date, refresh_time) VALUES (?, ?, ?)",
                (qq, date, refresh_time)
            )
            conn.commit()
            logger.info(f"记录用户 {qq} 的刷新操作: {date}")
        except Exception as e:
            logger.error(f"记录用户 {qq} 的刷新操作失败: {e}")
            conn.rollback()
        finally:
            conn.close()
    
    def reset_daily_refresh_count(self, qq: str, date: str):
        """重置用户指定日期的刷新次数"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                "DELETE FROM refresh_logs WHERE qq = ? AND refresh_date = ?",
                (qq, date)
            )
            conn.commit()
            logger.info(f"重置用户 {qq} 的刷新次数: {date}")
        except Exception as e:
            logger.error(f"重置用户 {qq} 的刷新次数失败: {e}")
            conn.rollback()
        finally:
            conn.close()

