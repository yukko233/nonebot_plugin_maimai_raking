"""落雪查分器 OAuth 客户端。

落雪 OAuth 使用授权码流程：Bot 发送授权链接，用户完成授权后将授权码
（或完整回调链接）发回 Bot。访问令牌和刷新令牌按 QQ 单独保存。
"""

import asyncio
import re
import secrets
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from nonebot.log import logger

from .oauth import OAuthError


class LxnsOAuthError(OAuthError):
    """落雪 OAuth 请求失败。"""


class LxnsOAuthNotConfigured(LxnsOAuthError):
    """没有配置落雪 OAuth 应用。"""


class LxnsOAuthConsentRequired(LxnsOAuthError):
    """用户尚未完成落雪 OAuth 授权。"""


class LxnsOAuthQuotaExceeded(LxnsOAuthError):
    """落雪 API 请求频率或配额受限。"""


class LxnsOAuthManager:
    """管理落雪 OAuth 令牌及授权码绑定会话。"""

    TOKEN_ENDPOINT = "/api/v0/oauth/token"
    AUTHORIZE_ENDPOINT = "/oauth/authorize"

    def __init__(
        self,
        db,
        client_id: str,
        client_secret: str,
        scope: str = "read_player",
        base_url: str = "https://maimai.lxns.net",
    ):
        self.db = db
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.scope = scope.strip() or "read_player"
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=15.0)
        self._locks: Dict[str, asyncio.Lock] = {}
        self._pending: Dict[str, Tuple[str, float]] = {}

    @property
    def is_configured(self) -> bool:
        # 落雪应用可以勾选“无回调地址”，此时授权成功后会直接显示 code。
        return bool(self.client_id and self.client_secret)

    def _ensure_configured(self):
        if not self.is_configured:
            raise LxnsOAuthNotConfigured(
                "未配置落雪 OAuth 应用，请设置客户端 ID 和客户端密钥。",
                code="not_configured",
            )

    def _get_lock(self, qq: str) -> asyncio.Lock:
        if qq not in self._locks:
            self._locks[qq] = asyncio.Lock()
        return self._locks[qq]

    @staticmethod
    def _json(response: httpx.Response) -> Dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}

    @classmethod
    def _error_from_response(cls, response: httpx.Response) -> LxnsOAuthError:
        payload = cls._json(response)
        code = str(payload.get("error") or payload.get("code") or "")
        message = str(
            payload.get("error_description")
            or payload.get("message")
            or payload.get("detail")
            or f"落雪 OAuth 请求失败（HTTP {response.status_code}）"
        )
        if code in {"invalid_grant", "invalid_token", "token_revoked"}:
            return LxnsOAuthConsentRequired(message, code=code, status_code=response.status_code)
        if response.status_code == 429:
            return LxnsOAuthQuotaExceeded(message, code=code or "rate_limited", status_code=429)
        return LxnsOAuthError(message, code=code, status_code=response.status_code)

    async def _token_request(self, data: Dict[str, str]) -> Dict[str, Any]:
        url = f"{self.base_url}{self.TOKEN_ENDPOINT}"
        try:
            response = await self.client.post(url, json=data)
        except httpx.HTTPError as exc:
            logger.warning(f"落雪 OAuth Token 请求失败: {type(exc).__name__}")
            raise LxnsOAuthError("连接落雪 OAuth 服务失败。", code="network_error") from exc

        if response.status_code < 200 or response.status_code >= 300:
            raise self._error_from_response(response)
        payload = self._json(response)
        if not payload.get("access_token"):
            raise LxnsOAuthError("落雪 OAuth 响应中没有 access_token。", code="invalid_response")
        return payload

    @staticmethod
    def _expires_at(payload: Dict[str, Any]) -> int:
        try:
            expires_in = max(0, int(payload.get("expires_in", 900)))
        except (TypeError, ValueError):
            expires_in = 900
        return int(time.time()) + expires_in

    async def _save_token(
        self,
        qq: str,
        payload: Dict[str, Any],
        previous: Optional[Dict[str, Any]] = None,
    ) -> str:
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise LxnsOAuthError("落雪 OAuth 响应中没有 access_token。", code="invalid_response")
        refresh_token = payload.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            refresh_token = (previous or {}).get("refresh_token")
        scope = payload.get("scope") or (previous or {}).get("scope") or self.scope
        await self.db.save_lxns_oauth_tokens(
            qq=str(qq),
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=self._expires_at(payload),
            scope=str(scope),
        )
        return access_token

    async def _refresh_token(self, qq: str, stored: Dict[str, Any]) -> str:
        payload = await self._token_request(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": str(stored["refresh_token"]),
            }
        )
        return await self._save_token(qq, payload, previous=stored)

    async def get_access_token(self, qq: str, force_refresh: bool = False) -> str:
        """获取有效的落雪 Access Token，必要时自动刷新。"""
        self._ensure_configured()
        qq = str(qq)
        async with self._get_lock(qq):
            stored = await self.db.get_lxns_oauth_tokens(qq)
            now = int(time.time())
            if (
                not force_refresh
                and stored
                and stored.get("access_token")
                and int(stored.get("expires_at") or 0) > now + 30
            ):
                return str(stored["access_token"])

            if stored and stored.get("refresh_token"):
                try:
                    return await self._refresh_token(qq, stored)
                except LxnsOAuthConsentRequired:
                    await self.db.clear_lxns_oauth_tokens(qq)
                    raise

            raise LxnsOAuthConsentRequired(
                "当前用户尚未绑定落雪账号。",
                code="consent_required",
            )

    def build_authorization_url(self, qq: str) -> str:
        """创建一次授权会话并返回落雪授权链接。"""
        self._ensure_configured()
        state = secrets.token_urlsafe(24)
        self._pending[str(qq)] = (state, time.monotonic() + 15 * 60)
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.client_id,
                "scope": self.scope,
                "state": state,
            }
        )
        return f"{self.base_url}{self.AUTHORIZE_ENDPOINT}?{query}"

    @staticmethod
    def extract_authorization_response(text: str) -> Tuple[Optional[str], Optional[str]]:
        """从授权码、授权码前缀或完整回调链接中提取 code/state。"""
        value = text.strip()
        if not value:
            return None, None

        parsed = urlparse(value)
        # 兼容 HTTP(S)、自定义 URI scheme，以及无回调模式返回的纯授权码。
        if parsed.scheme and (parsed.query or parsed.fragment):
            query = parse_qs(parsed.query)
            fragment = parse_qs(parsed.fragment)
            code = (query.get("code") or fragment.get("code") or [None])[0]
            state = (query.get("state") or fragment.get("state") or [None])[0]
            return (code, state) if code else (None, None)

        prefixed = re.fullmatch(r"授权码\s*[:：]?\s*(\S+)", value)
        if prefixed:
            value = prefixed.group(1)

        # 落雪当前授权码通常是 XXXX-XXXX-XXXX；同时兼容文档中的长随机码。
        if (
            11 <= len(value) <= 256
            and all(char.isalnum() or char in "-_" for char in value)
        ):
            return value, None
        return None, None

    def _check_pending(self, qq: str, state: Optional[str]):
        pending = self._pending.get(str(qq))
        if not pending or pending[1] <= time.monotonic():
            self._pending.pop(str(qq), None)
            raise LxnsOAuthError(
                "请先发送「绑定落雪账号」获取新的授权链接。",
                code="binding_session_missing",
            )
        expected_state, _ = pending
        if state and state != expected_state:
            raise LxnsOAuthError("落雪 OAuth state 校验失败，请重新发起绑定。", code="invalid_state")

    async def exchange_code(self, qq: str, code: str, state: Optional[str] = None):
        """兑换授权码并保存落雪 OAuth 令牌。"""
        self._ensure_configured()
        qq = str(qq)
        self._check_pending(qq, state)
        payload = await self._token_request(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "authorization_code",
                "code": code,
            }
        )
        await self._save_token(qq, payload)
        self._pending.pop(qq, None)

    async def clear_tokens(self, qq: str):
        """清除本地令牌；落雪未提供标准撤销端点时由用户在网页端撤销授权。"""
        self._pending.pop(str(qq), None)
        await self.db.clear_lxns_oauth_tokens(str(qq))

    async def close(self):
        await self.client.aclose()
