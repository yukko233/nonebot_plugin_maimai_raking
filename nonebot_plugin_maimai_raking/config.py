"""配置模块"""
from pydantic import BaseModel, Field


class Config(BaseModel):
    """插件配置"""

    maimai_oauth_client_id: str = Field(
        default="",
        description="水鱼 OAuth 应用的 client_id"
    )
    maimai_oauth_client_secret: str = Field(
        default="",
        description="水鱼 OAuth 应用的 client_secret"
    )
    maimai_oauth_scope: str = Field(
        default="prober.records.read",
        description="水鱼 OAuth Scope"
    )
    maimai_oauth_base_url: str = Field(
        default="https://auth.diving-fish.com",
        description="水鱼 OAuth 授权服务器地址"
    )

    model_config = {"extra": "ignore"}
