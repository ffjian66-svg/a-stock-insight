"""微信推送的两个端点：发一条测试消息、读账本。

## 为什么只需要这两个

webhook 地址**只存服务器 `.env`**，没有"写配置"的接口：这个站点没有鉴权（只有
`TrustedHostMiddleware` 限制 Host 头），一个能改 webhook 的接口等于一个开放的骚扰接口
——改成一个攻击者的地址，之后每晚的候选名单就都发到别人群里去了。
所以补偿是只读的账本 + 一个限流的测试按钮。

## 测试按钮为什么**不**按天去重

`push` 的幂等键是日报的全部依靠，但用在测试消息上恰好是错的：换了 webhook 之后按一下，
若被去重挡住，界面会显示上一行 `sent`——**看上去像新密钥通了，其实一条都没发**。
所以测试消息的键带**分钟**，并把 `reused` 明说出来。这一分钟最多一次的约束不在键上，
在下面的限流器上（进程内、1 次/分钟）。
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import NotifyEventView, NotifyTestResult
from app.core.config import get_settings
from app.db.models import NotifyEvent
from app.db.session import get_db
from app.providers.base import ProviderRateLimitError
from app.providers.rate_limiter import SlidingWindowRateLimiter
from app.services import notify

notify_router = APIRouter(prefix="/api/v1/notify", tags=["notify"])

# 1 次/分钟，与键里的分钟粒度一致。`daily_budget` 给 60：它不是配额（企业微信的额度由
# notify 侧的 20 条/分钟决定），只是"密钥泄漏后最多被刷一天"的兜底。
_TEST_LIMITER = SlidingWindowRateLimiter(calls_per_minute=1, daily_budget=60)

_MAX_LIMIT = 100


def _event_payload(event: NotifyEvent) -> dict:
    """账本行 → 响应字段。`status_label` 在这里补，前端不重写一遍映射。"""
    return {
        "id": event.id,
        "kind": event.kind,
        "status": event.status,
        "status_label": notify.STATUS_LABELS.get(event.status, event.status),
        "http_status": event.http_status,
        "errcode": event.errcode,
        "error": event.error,
        "byte_len": event.byte_len,
        "summary": event.summary,
        "content": event.content,
        "created_at": event.created_at,
    }


@notify_router.post("/test", response_model=NotifyTestResult)
def send_test(db: Session = Depends(get_db)) -> NotifyTestResult:
    """发一条固定文案的测试消息。**无请求体**——因此不存在攻击者可控的内容。

    残余风险照实说：站点没有鉴权，任何拿到这个 IP 的人都能让机器人每分钟发一条固定文本
    （有 `TrustedHostMiddleware` 限制 Host 头，但那只挡域名混淆）。所以文案不可定制、
    频率限死，能把后果压到"群里多一条测试消息"。

    发送失败**仍然返回 200**：这是"请求被受理了、结果是失败"，结果本身在 `status` /
    `status_label` / `error` 里，与账本里那行逐字一致。用 5xx 表达它会让前端不得不再造一套
    错误语义，而账本才是唯一事实。
    """
    settings = get_settings()
    if not settings.notify_configured:
        # 先判配置再限流：没配的时候不该消耗限额（否则配好了还要等一分钟）。
        raise HTTPException(
            status_code=400,
            detail="未配置微信推送：请在服务器 .env 里填 WECOM_WEBHOOK_URL（企业微信群机器人"
            "地址），然后重启服务——配置是启动时读入的。",
        )
    try:
        _TEST_LIMITER.acquire()
    except ProviderRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    started_at = datetime.now()
    event = notify.push(
        db,
        kind="test",
        key=notify.dedup_key("test", date.today(), started_at.strftime("%H:%M")),
        content=notify.render_test(),
    )
    # 先把字段取出来再拼返回值：`push` 之后这个对象可能已被 `rollback()` 过期，
    # 那时再读属性会触发一次隐式刷新（虽然行已提交、读得回来，但没必要）。
    payload = _event_payload(event)
    reused = event.created_at < started_at
    if reused:
        detail = "这一分钟内已经发过一条测试消息，本次没有重发（上面是那一条的结果）。"
    elif event.status == "sent":
        detail = "已发出。手机上没收到就是群机器人被移出群或地址已失效——账本里会记 errcode。"
    else:
        detail = f"没发出去：{event.error or '企业微信未返回 errmsg'}"
    return NotifyTestResult(**payload, reused=reused, detail=detail)


@notify_router.get("/events", response_model=list[NotifyEventView])
def list_events(
    limit: int = Query(20, ge=1, le=_MAX_LIMIT), db: Session = Depends(get_db)
) -> list[NotifyEventView]:
    """「最近推送」：倒序。按 `id` 而不按 `created_at`（墙钟没有唯一性，同一秒的两条会掷骰子）。"""
    rows = db.scalars(select(NotifyEvent).order_by(NotifyEvent.id.desc()).limit(limit)).all()
    return [NotifyEventView(**_event_payload(row)) for row in rows]
