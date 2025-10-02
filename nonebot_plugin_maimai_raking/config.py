"""配置模块"""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


class Config(BaseSettings):
    """插件配置"""
    
    # 水鱼查分器 Developer Token（必填）
    maimai_developer_token: str = Field(
        default="",
        description="水鱼查分器 Developer Token，在官网申请获取"
    )
    
    # 数据存储路径（可选）
    maimai_data_path: Path = Field(
        default=Path("data/maimai_raking"),
        description="数据存储路径"
    )
    
    model_config = SettingsConfigDict(
        extra="ignore",
        env_file=".env"
    )

