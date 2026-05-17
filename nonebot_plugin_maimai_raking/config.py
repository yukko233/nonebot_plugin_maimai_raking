"""配置模块"""
from pydantic import BaseModel, Field


class Config(BaseModel):
    """插件配置"""

    maimai_developer_token: str = Field(
        default="",
        description="水鱼查分器 Developer Token，在官网申请获取"
    )

    model_config = {"extra": "ignore"}
