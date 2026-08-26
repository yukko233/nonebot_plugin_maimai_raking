"""水鱼账号 OAuth 客户端。

这个模块只负责授权服务器交互和本地 Token 生命周期管理。成绩 API
本身不会接触 client secret，也不会再使用已经废弃的 Developer-Token。
"""

import asyncio
import hashlib
import time
from typing import Any, Dict, Optional
from urllib.parse import urljoin

import httpx
from nonebot.log import logger


class OAuthError(Exception):
    """OAuth 请求失败。"""

    def __init__(self, message: str, code: str = "", status_code: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


class OAuthNotConfigured(OAuthError):
    """插件没有配置 OAuth 应用凭据。"""


class OAuthConsentRequired(OAuthError):
    """换票需要用户完成或重新完成授权。"""


class OAuthQuotaExceeded(OAuthError):
    """服务端返回今日调用配额已用尽。"""


class OAuthRateLimited(OAuthError):
    """授权服务器限制了短时间内的换票请求。"""


class OAuthManager:
    """管理水鱼 OAuth Token，并把 Token 绑定到本地 QQ 用户。"""

    TOKEN_ENDPOINT = "/oauth/token"
    DEVICE_AUTHORIZATION_ENDPOINT = "/oauth/device_authorization"
    REVOKE_ENDPOINT = "/oauth/revoke"
    DISCOVERY_ENDPOINT = "/.well-known/oauth-authorization-server"

    def __init__(
        self,
        db,
        client_id: str,
        client_secret: str,
        scope: str = "prober.records.read",
        authorization_server: str = "https://auth.diving-fish.com",
    ):
        self.db = db
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.scope = scope.strip() or "prober.records.read"
        self.authorization_server = authorization_server.rstrip("/")
        self.client = httpx.AsyncClient(timeout=10.0)
        self._locks: Dict[str, asyncio.Lock] = {}
        self._discovery: Dict[str, Any] = {}
        self._discovery_expires_at = 0.0
        self._discovery_lock: Optional[asyncio.Lock] = None

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def _get_lock(self, qq: str) -> asyncio.Lock:
        if qq not in self._locks:
            self._locks[qq] = asyncio.Lock()
        return self._locks[qq]

    def subject_ref(self, qq: str) -> str:
        """按官方规则生成本应用自己的用户标识摘要。"""
        return hashlib.sha256(f"{self.client_id}:{qq}".encode()).hexdigest()

    @staticmethod
    def binding_label(qq: str) -> str:
        """设备确认页展示脱敏后的本地身份，避免暴露完整 QQ 号。"""
        qq = str(qq)
        return f"QQ {qq[:2]}****{qq[-2:]}" if len(qq) > 4 else "QQ ****"

    def _ensure_configured(self):
        if not self.is_configured:
            raise OAuthNotConfigured(
                "未配置水鱼 OAuth 应用，请设置 MAIMAI_OAUTH_CLIENT_ID "
                "和 MAIMAI_OAUTH_CLIENT_SECRET。"
            )

    @staticmethod
    def _json(response: httpx.Response) -> Dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}

    @classmethod
    def _error_from_response(cls, response: httpx.Response) -> OAuthError:
        payload = cls._json(response)
        code = str(payload.get("error") or payload.get("code") or "")
        message = str(
            payload.get("error_description")
            or payload.get("message")
            or payload.get("detail")
            or f"OAuth 请求失败（HTTP {response.status_code}）"
        )
        if code == "consent_required" or "consent_required" in message:
            return OAuthConsentRequired(message, code=code, status_code=response.status_code)
        if code == "slow_down":
            return OAuthRateLimited(message, code=code, status_code=response.status_code)
        if response.status_code == 429:
            return OAuthQuotaExceeded(message, code=code or "quota_exceeded", status_code=429)
        return OAuthError(message, code=code, status_code=response.status_code)

    def _token_expiry(self, payload: Dict[str, Any]) -> int:
        expires_in = payload.get("expires_in", 3600)
        try:
            return int(time.time()) + max(0, int(expires_in))
        except (TypeError, ValueError):
            return int(time.time()) + 3600

    async def _post(self, url: str, data: Dict[str, str]) -> httpx.Response:
        try:
            return await self.client.post(url, data=data)
        except httpx.HTTPError as exc:
            raise OAuthError(f"连接水鱼 OAuth 服务失败：{exc}") from exc

    async def _endpoint(self, name: str, fallback: str) -> str:
        """从 OAuth Discovery 获取端点，Discovery 不可用时使用文档默认路径。"""
        now = time.monotonic()
        if now >= self._discovery_expires_at:
            if self._discovery_lock is None:
                self._discovery_lock = asyncio.Lock()
            async with self._discovery_lock:
                if time.monotonic() >= self._discovery_expires_at:
                    try:
                        response = await self.client.get(
                            f"{self.authorization_server}{self.DISCOVERY_ENDPOINT}"
                        )
                        if 200 <= response.status_code < 300:
                            payload = self._json(response)
                            if payload:
                                self._discovery = payload
                                # 端点变化无需频繁探测，5 分钟刷新一次即可。
                                self._discovery_expires_at = time.monotonic() + 300
                            else:
                                self._discovery = {}
                                self._discovery_expires_at = time.monotonic() + 60
                        else:
                            self._discovery = {}
                            self._discovery_expires_at = time.monotonic() + 60
                    except httpx.HTTPError:
                        # Discovery 失败时仍可使用当前官方默认路径，避免临时故障阻断授权。
                        self._discovery = {}
                        self._discovery_expires_at = time.monotonic() + 60

        endpoint = self._discovery.get(name)
        if isinstance(endpoint, str) and endpoint:
            return urljoin(f"{self.authorization_server}/", endpoint)
        return f"{self.authorization_server}{fallback}"

    async def _save_token(
        self,
        qq: str,
        payload: Dict[str, Any],
        previous: Optional[Dict[str, Any]] = None,
    ) -> str:
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise OAuthError("OAuth 响应中没有 access_token。")

        refresh_token = payload.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            refresh_token = (previous or {}).get("refresh_token")

        subject = payload.get("subject") or payload.get("sub") or (previous or {}).get("subject")
        scope = payload.get("scope") or (previous or {}).get("scope") or self.scope
        await self.db.save_oauth_tokens(
            qq=qq,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=self._token_expiry(payload),
            subject=str(subject) if subject else None,
            scope=str(scope),
        )
        return access_token

    async def _token_request(self, data: Dict[str, str]) -> Dict[str, Any]:
        endpoint = await self._endpoint("token_endpoint", self.TOKEN_ENDPOINT)
        response = await self._post(
            endpoint,
            data=data,
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise self._error_from_response(response)
        payload = self._json(response)
        if not payload:
            raise OAuthError("OAuth 响应不是有效 JSON。", status_code=response.status_code)
        return payload

    async def _refresh_token(self, qq: str, stored: Dict[str, Any]) -> str:
        data = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "refresh_token": stored["refresh_token"],
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret
        payload = await self._token_request(data)
        return await self._save_token(qq, payload, previous=stored)

    async def _exchange_token(self, qq: str, subject: str) -> str:
        """使用 OBO 流程换取代表本地用户的 OAuth Token。"""
        payload = await self._token_request(
            {
                "grant_type": "urn:diving-fish:params:oauth:grant-type:on-behalf-of",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "subject": subject,
                "scope": self.scope,
            }
        )
        return await self._save_token(qq, payload)

    async def get_access_token(self, qq: str, force_refresh: bool = False) -> str:
        """获取本地用户的有效 Access Token。

        优先使用缓存或 Refresh Token；没有存量授权时，最后尝试文档规定的
        迁移期 OBO。OBO 返回 consent_required 时由上层提示用户走设备码授权。
        """
        self._ensure_configured()
        qq = str(qq)
        async with self._get_lock(qq):
            stored = await self.db.get_oauth_tokens(qq)
            now = int(time.time())

            if (
                not force_refresh
                and stored
                and stored.get("access_token")
                and int(stored.get("expires_at") or 0) > now + 60
            ):
                return str(stored["access_token"])

            if stored and stored.get("refresh_token"):
                try:
                    return await self._refresh_token(qq, stored)
                except OAuthError as exc:
                    # 只有 Refresh Token 失效时才丢弃本地 Token 并继续尝试迁移。
                    # 服务端错误、限流等情况不能通过重复换票解决。
                    if exc.code not in {"invalid_grant", "invalid_token", "token_revoked"}:
                        raise
                    await self.db.clear_oauth_tokens(qq)

            # ref 是长期方案；qq 仅作为 Developer-Token 迁移期兼容回退。
            # 这样新用户绑定后不依赖即将停止的 qq subject，存量用户仍可无感换票。
            try:
                return await self._exchange_token(qq, f"ref:{self.subject_ref(qq)}")
            except OAuthConsentRequired as ref_error:
                try:
                    return await self._exchange_token(qq, f"qq:{qq}")
                except OAuthError:
                    raise ref_error

    async def invalidate_access_token(self, qq: str):
        """收到 API 401 时仅清除 Access Token，保留 Refresh Token。"""
        await self.db.clear_oauth_access_token(str(qq))

    async def start_device_authorization(self, qq: str) -> Dict[str, Any]:
        """申请设备码，并返回给用户展示所需的信息。"""
        self._ensure_configured()
        data = {
            "client_id": self.client_id,
            "scope": self.scope,
            "subject_ref": self.subject_ref(str(qq)),
            "binding_label": self.binding_label(str(qq)),
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret

        endpoint = await self._endpoint(
            "device_authorization_endpoint", self.DEVICE_AUTHORIZATION_ENDPOINT
        )
        response = await self._post(
            endpoint,
            data=data,
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise self._error_from_response(response)
        payload = self._json(response)
        if not payload.get("device_code") or not payload.get("user_code"):
            raise OAuthError("设备授权响应缺少 device_code 或 user_code。")
        return payload

    async def poll_device_authorization(self, qq: str, device: Dict[str, Any]) -> str:
        """轮询设备码，直到用户完成授权或设备码过期。"""
        expires_in = device.get("expires_in", 600)
        try:
            timeout = max(1, int(expires_in))
        except (TypeError, ValueError):
            timeout = 600
        try:
            interval = max(1, int(device.get("interval", 5)))
        except (TypeError, ValueError):
            interval = 5

        data = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": str(device["device_code"]),
            "client_id": self.client_id,
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret

        deadline = time.monotonic() + timeout
        endpoint = await self._endpoint("token_endpoint", self.TOKEN_ENDPOINT)
        while time.monotonic() < deadline:
            # 按服务端返回的 interval 等待后再首次轮询，避免触发 slow_down。
            await asyncio.sleep(interval)
            if time.monotonic() >= deadline:
                break
            response = await self._post(
                endpoint,
                data=data,
            )
            payload = self._json(response)
            if 200 <= response.status_code < 300 and payload.get("access_token"):
                return await self._save_token(qq, payload)

            code = str(payload.get("error") or payload.get("code") or "")
            if code in {"authorization_pending", "slow_down"}:
                if code == "slow_down":
                    interval += 5
                continue

            if response.status_code >= 400:
                raise self._error_from_response(response)
            raise OAuthError("设备授权响应中没有 access_token。")

        raise OAuthError("设备码已过期，请重新发送绑定命令。", code="expired_token")

    async def revoke_user(self, qq: str):
        """撤销远端 Token，并删除本地保存的授权信息。"""
        stored = await self.db.get_oauth_tokens(str(qq))
        if stored and stored.get("access_token") and self.is_configured:
            data = {
                "token": str(stored["access_token"]),
                "token_type_hint": "access_token",
                "client_id": self.client_id,
            }
            if self.client_secret:
                data["client_secret"] = self.client_secret
            try:
                endpoint = await self._endpoint("revocation_endpoint", self.REVOKE_ENDPOINT)
                response = await self._post(
                    endpoint,
                    data=data,
                )
                if response.status_code >= 400:
                    logger.warning("撤销水鱼 OAuth Token 失败，已继续清理本地授权")
            except (httpx.HTTPError, OAuthError):
                logger.warning("撤销水鱼 OAuth Token 网络失败，已继续清理本地授权")
        await self.db.delete_oauth_tokens(str(qq))

    async def close(self):
        await self.client.aclose()
