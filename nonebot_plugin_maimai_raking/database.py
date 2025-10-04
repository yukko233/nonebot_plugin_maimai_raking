"""数据库模块 - 使用 JSON 文件存储数据"""
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
        
        # 数据文件路径
        self.groups_file = self.data_path / "groups.json"
        self.users_file = self.data_path / "users.json"
        self.records_file = self.data_path / "records.json"
        
        # 加载数据
        self.groups: Dict[str, dict] = self._load_json(self.groups_file, {})
        self.users: Dict[str, dict] = self._load_json(self.users_file, {})
        self.records: Dict[str, dict] = self._load_json(self.records_file, {})
    
    def _load_json(self, file_path: Path, default: Any) -> Any:
        """加载 JSON 文件"""
        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"加载 {file_path} 失败: {e}")
                return default
        return default
    
    def _save_json(self, file_path: Path, data: Any):
        """保存 JSON 文件"""
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存 {file_path} 失败: {e}")
    
    # ==================== 群组管理 ====================
    
    def enable_group(self, group_id: str):
        """开启群组功能"""
        if group_id not in self.groups:
            self.groups[group_id] = {
                "enabled": True,
                "users": [],
                "created_at": datetime.now().isoformat(),
            }
        else:
            self.groups[group_id]["enabled"] = True
        
        self._save_json(self.groups_file, self.groups)
        logger.info(f"群组 {group_id} 已开启舞萌排行榜功能")
    
    def disable_group(self, group_id: str):
        """关闭群组功能"""
        if group_id in self.groups:
            self.groups[group_id]["enabled"] = False
            self._save_json(self.groups_file, self.groups)
            logger.info(f"群组 {group_id} 已关闭舞萌排行榜功能")
    
    def is_group_enabled(self, group_id: str) -> bool:
        """检查群组是否开启功能"""
        return self.groups.get(group_id, {}).get("enabled", False)
    
    # ==================== 用户管理 ====================
    
    def add_user_to_group(self, qq: str, group_id: str):
        """添加用户到群组"""
        if group_id not in self.groups:
            self.enable_group(group_id)
        
        if qq not in self.groups[group_id]["users"]:
            self.groups[group_id]["users"].append(qq)
            self._save_json(self.groups_file, self.groups)
        
        # 更新用户所属群组
        if qq not in self.users:
            self.users[qq] = {
                "groups": [],
                "joined_at": datetime.now().isoformat(),
            }
        
        if group_id not in self.users[qq]["groups"]:
            self.users[qq]["groups"].append(group_id)
            self._save_json(self.users_file, self.users)
        
        logger.info(f"用户 {qq} 已加入群组 {group_id} 的排行榜")
    
    def remove_user_from_group(self, qq: str, group_id: str):
        """从群组移除用户"""
        if group_id in self.groups and qq in self.groups[group_id]["users"]:
            self.groups[group_id]["users"].remove(qq)
            self._save_json(self.groups_file, self.groups)
        
        if qq in self.users and group_id in self.users[qq]["groups"]:
            self.users[qq]["groups"].remove(group_id)
            self._save_json(self.users_file, self.users)
        
        logger.info(f"用户 {qq} 已从群组 {group_id} 的排行榜中退出")
    
    def is_user_in_group(self, qq: str, group_id: str) -> bool:
        """检查用户是否在群组中"""
        return qq in self.groups.get(group_id, {}).get("users", [])
    
    def get_group_users(self, group_id: str) -> List[str]:
        """获取群组的所有用户"""
        return self.groups.get(group_id, {}).get("users", [])
    
    def get_all_users(self) -> List[str]:
        """获取所有用户"""
        return list(self.users.keys())
    
    def get_all_enabled_groups(self) -> List[str]:
        """获取所有启用的群组"""
        enabled_groups = []
        for group_id, group_data in self.groups.items():
            if group_data.get("enabled", False):
                enabled_groups.append(group_id)
        return enabled_groups
    
    # ==================== 成绩管理 ====================
    
    def update_user_records(self, qq: str, records: dict):
        """更新用户成绩"""
        self.records[qq] = {
            "data": records,
            "updated_at": datetime.now().isoformat(),
        }
        self._save_json(self.records_file, self.records)
        logger.info(f"用户 {qq} 的成绩已更新")
    
    def get_user_records(self, qq: str) -> Optional[dict]:
        """获取用户成绩"""
        # 重新从文件加载以确保数据最新
        self.records = self._load_json(self.records_file, {})
        return self.records.get(qq, {}).get("data")
    
    def get_last_update_time(self, qq: str) -> Optional[str]:
        """获取用户成绩的最后更新时间"""
        return self.records.get(qq, {}).get("updated_at")

