from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import socket
import time
import urllib.parse
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.core.errors import AppError

LONG_SEGMENT_MIN = 20
MAX_RESPONSE_SNIPPET = 200


@dataclass(frozen=True)
class SendResult:
    ok: bool
    error: str | None = None
    status_code: int | None = None


def _mask_value(value: str) -> str:
    if len(value) <= 6:
        return "*" * len(value)
    return f"{value[:3]}***{value[-2:]}"


def _mask_segment(segment: str) -> str:
    # 令牌常出现在 webhook 路径段里，长且不带扩展名的段一律按令牌处理。
    if len(segment) >= LONG_SEGMENT_MIN and "." not in segment:
        return _mask_value(segment)
    return segment


def mask_webhook_url(url: str) -> str:
    """Return a display-safe form of a webhook URL, hiding embedded credentials."""
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return "***"
    netloc = parsed.netloc.rsplit("@", 1)[-1]
    path = "/".join(_mask_segment(part) for part in parsed.path.split("/"))
    # 查询串里既可能有 access_token 也可能有普通参数，展示时统一打码，避免凭证外泄。
    query = "&".join(f"{key}={_mask_value(value)}" for key, value in urllib.parse.parse_qsl(parsed.query))
    return urllib.parse.urlunsplit((parsed.scheme, netloc, path, query, ""))


def _check_public_host(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise AppError("NOTIFY_URL_INVALID", f"通知渠道地址无法解析：{host}", 422) from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            raise AppError(
                "NOTIFY_URL_INVALID",
                f"通知渠道地址指向内网或保留网段：{host}，如确需推送请设置 NOTIFY_ALLOW_PRIVATE_TARGET",
                422,
            )


def validate_webhook_url(url: str) -> str:
    """Reject URLs that could turn the backend into an internal-network probe."""
    cleaned = (url or "").strip()
    if not cleaned:
        raise AppError("NOTIFY_URL_INVALID", "通知渠道地址不能为空", 422)
    if len(cleaned) > 1000:
        raise AppError("NOTIFY_URL_INVALID", "通知渠道地址过长", 422)
    try:
        parsed = urllib.parse.urlsplit(cleaned)
    except ValueError as exc:
        raise AppError("NOTIFY_URL_INVALID", "通知渠道地址格式不合法", 422) from exc
    if parsed.scheme == "https":
        pass
    elif parsed.scheme == "http" and not settings.is_production:
        pass
    else:
        raise AppError("NOTIFY_URL_INVALID", "通知渠道地址必须使用 HTTPS", 422)
    if not parsed.netloc or not parsed.hostname:
        raise AppError("NOTIFY_URL_INVALID", "通知渠道地址缺少主机名", 422)
    if parsed.username or parsed.password or "@" in parsed.netloc:
        raise AppError("NOTIFY_URL_INVALID", "通知渠道地址不允许包含账号密码", 422)
    if parsed.fragment:
        raise AppError("NOTIFY_URL_INVALID", "通知渠道地址不允许包含锚点", 422)
    # 默认端口之外还有内网校验一起放开：本地联调的接收端通常就跑在非默认端口上
    if parsed.port not in (None, 80, 443) and not settings.notify_allow_private_target:
        raise AppError("NOTIFY_URL_INVALID", "通知渠道地址只允许默认端口", 422)
    if not settings.notify_allow_private_target:
        _check_public_host(parsed.hostname)
    return cleaned


def _signed_dingtalk(url: str, secret: str) -> str:
    timestamp = str(round(time.time() * 1000))
    digest = hmac.new(secret.encode("utf-8"), f"{timestamp}\n{secret}".encode(), hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest).decode("utf-8"))
    separator = "&" if urllib.parse.urlsplit(url).query else "?"
    return f"{url}{separator}timestamp={timestamp}&sign={sign}"


def _signed_feishu(secret: str) -> tuple[str, str]:
    # 飞书自定义机器人：以 "时间戳\n密钥" 作为 key 对空串做 HmacSHA256，再 Base64。
    timestamp = str(int(time.time()))
    digest = hmac.new(f"{timestamp}\n{secret}".encode(), b"", hashlib.sha256).digest()
    return timestamp, base64.b64encode(digest).decode("utf-8")


def _markdown_body(channel_type: str, title: str, content: str) -> dict:
    text = f"### {title}\n\n{content}" if channel_type == "dingtalk" else f"**{title}**\n{content}"
    if channel_type == "dingtalk":
        return {"msgtype": "markdown", "markdown": {"title": title, "text": text}}
    if channel_type == "wecom":
        return {"msgtype": "markdown", "markdown": {"content": text}}
    if channel_type == "feishu":
        return {"msg_type": "text", "content": {"text": f"{title}\n{content}"}}
    return {"title": title, "content": content, "markdown": text}


def _judge(channel_type: str, response: httpx.Response) -> SendResult:
    code = response.status_code
    snippet = response.text[:MAX_RESPONSE_SNIPPET].strip()
    if code >= 400:
        return SendResult(False, f"HTTP {code}: {snippet}", code)
    if 300 <= code < 400:
        # 跟随重定向会让外网校验过的地址跳到内网，这里直接拒绝。
        return SendResult(False, f"HTTP {code}: 通知渠道不允许重定向", code)
    if channel_type == "webhook":
        return SendResult(True, None, code)
    try:
        payload = response.json()
    except ValueError:
        return SendResult(False, f"响应不是 JSON：{snippet}", code)
    # 钉钉与企业微信用 errcode，飞书新版用 code，都要求为 0 才算成功。
    for key in ("errcode", "code"):
        if key in payload:
            value = payload.get(key)
            if value in (0, "0"):
                return SendResult(True, None, code)
            message = str(payload.get("errmsg") or payload.get("msg") or snippet)
            return SendResult(False, f"{key}={value}: {message}", code)
    return SendResult(False, f"响应缺少返回码：{snippet}", code)


async def send_markdown(
    *,
    channel_type: str,
    webhook_url: str,
    secret: str | None,
    title: str,
    content: str,
    event: str,
    dedupe_key: str,
) -> SendResult:
    url = webhook_url
    body = _markdown_body(channel_type, title, content)
    if secret and channel_type == "dingtalk":
        url = _signed_dingtalk(webhook_url, secret)
    elif secret and channel_type == "feishu":
        timestamp, sign = _signed_feishu(secret)
        body = {**body, "timestamp": timestamp, "sign": sign}
    elif secret and channel_type == "wecom":
        return SendResult(False, "企业微信机器人不支持加签，请清空密钥或改用钉钉", None)
    elif channel_type == "webhook":
        body = {**body, "event": event, "dedupe_key": dedupe_key}
    try:
        async with httpx.AsyncClient(timeout=settings.notify_timeout_seconds, follow_redirects=False) as client:
            response = await client.post(url, json=body, headers={"User-Agent": "FastCMS-Notify/1.0"})
    except httpx.HTTPError as exc:
        return SendResult(False, f"{type(exc).__name__}: {exc}"[:512], None)
    return _judge(channel_type, response)
