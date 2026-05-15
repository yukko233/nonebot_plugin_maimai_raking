"""配置模块"""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


def _default_data_path() -> Path:
    import nonebot_plugin_localstore as store
    return store.get_plugin_data_dir()


def _default_cache_path() -> Path:
    import nonebot_plugin_localstore as store
    return store.get_plugin_cache_dir()


class Config(BaseSettings):
    """插件配置"""

    maimai_developer_token: str = Field(
        default="",
        description="水鱼查分器 Developer Token，在官网申请获取"
    )

    maimai_data_path: Path = Field(
        default_factory=_default_data_path,
        description="数据存储路径"
    )

    maimai_cache_path: Path = Field(
        default_factory=_default_cache_path,
        description="缓存存储路径"
    )

    model_config = SettingsConfigDict(
        extra="ignore",
        env_file=".env"
    )

