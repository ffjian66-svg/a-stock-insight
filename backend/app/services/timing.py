"""操作时机标签（智能选股"推荐买入卖出时机"）。

规则引擎是纯函数 `decide_timing`：只消费 calculate_indicators 产出的技术指标
（ma5/ma20/rsi/volatility）与一个价格参考，返回 {'label','tone','detail'} 或 None。
tone ∈ buy|hold|reduce|watch。阈值镜像 scoring.py 的判据（ma5>ma20 为上行、
RSI>78 过热惩罚、RSI 45-68 良性带）。措辞为"时机"口吻，仅供研究参考。

`compute_timing_for_codes` 是薄加载器：按 code 批量取最近 ≤120 根收盘（正序），
算出指标后逐只调用 decide_timing，供 /screener 路由实时附加到候选行。
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.db.models import DailyBar
from app.services.indicators import calculate_indicators

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy.orm import Session

# ---- 规则表数值常量（镜像 scoring.py 的口径）----
MAX_BARS = 120  # 参与计算的最近收盘根数上限
MIN_BARS = 20  # 不足该根数无法计算指标，返回 None
OVERBOUGHT_RSI = 78.0  # RSI 超买阈值（scoring.py:29 此处 -10）
WEAK_RSI = 45.0  # 强势区间下沿（scoring.py:29 的 45-68 良性带）
STRONG_RSI = 68.0  # 强势区间上沿
EXTENDED_DEV = 0.10  # 相对 MA20 偏离 ≥10% 视为追高风险
TIGHT_DEV = 0.05  # 贴近 MA20（现价可分批区）
PULLBACK_DEV = 0.03  # 上行中回踩至 MA20 附近（下方 3% 内）
DEEP_DROP_DEV = 0.12  # 超卖反弹试错的最大深度（更深不接飞刀）
OVERSOLD_RSI = 30.0  # RSI 超卖阈值
MAX_VOL_FOR_OVERSOLD = 6.0  # 高波动时不建议抄底


def _fmt_ma(ma20: float) -> str:
    return f"{ma20:.0f}" if ma20 >= 100 else f"{ma20:.1f}"


def _fmt_dev(dev: float) -> str:
    return f"{dev * 100:.0f}%"


def decide_timing(
    price: float | None,
    ma5: float | None,
    ma20: float | None,
    rsi: float | None,
    volatility: float | None,
) -> dict[str, str] | None:
    """按顺序规则表生成操作时机标签；任一关键值缺失/非法时返回 None。"""
    values = (price, ma5, ma20)
    if any(v is None or not math.isfinite(v) or v <= 0 for v in values):
        return None
    assert price is not None and ma5 is not None and ma20 is not None  # 供类型收窄
    if rsi is not None and (not math.isfinite(rsi) or not 0 <= rsi <= 100):
        rsi = None
    if volatility is not None and not math.isfinite(volatility):
        volatility = None

    dev = price / ma20 - 1
    above = dev >= 0
    uptrend = ma5 > ma20

    label: str
    tone: str
    if above and rsi is not None and rsi > OVERBOUGHT_RSI:
        tone, label = "reduce", f"短线超买(RSI≈{rsi:.0f})，冲高减仓"
    elif above and dev >= EXTENDED_DEV:
        tone, label = "watch", f"偏离MA20约{_fmt_dev(dev)}，回踩不追高"
    elif not above and dev >= -PULLBACK_DEV and uptrend:
        tone, label = "buy", f"回调MA20(≈{_fmt_ma(ma20)})，可分批"
    elif above and dev < TIGHT_DEV and uptrend:
        tone, label = "buy", "现价贴近MA20上行，可分批"
    elif (
        above
        and dev < EXTENDED_DEV
        and uptrend
        and rsi is not None
        and WEAK_RSI <= rsi <= STRONG_RSI
    ):
        tone, label = "buy", f"沿MA20上行(RSI≈{rsi:.0f})，现价可分批"
    elif (
        above
        and uptrend
        and rsi is not None
        and (rsi < WEAK_RSI or STRONG_RSI < rsi <= OVERBOUGHT_RSI)
    ):
        tone, label = "hold", f"短线上行放缓(RSI≈{rsi:.0f})，持有待回踩"
    elif (
        not above
        and dev >= -DEEP_DROP_DEV
        and rsi is not None
        and rsi < OVERSOLD_RSI
        and (volatility is None or volatility <= MAX_VOL_FOR_OVERSOLD)
    ):
        tone, label = "buy", f"RSI超卖(≈{rsi:.0f})，轻仓分批试探"
    elif not above:
        tone, label = "watch", "均线下方观望，站回MA20再看"
    elif not uptrend:
        tone, label = "watch", "站上MA20但5日线走弱，观望"
    elif rsi is None:
        tone, label = "buy", "上行结构，现价或回踩分批"
    else:
        # 上行且贴近 MA20，但 RSI 信息不足以细分 → 视同现价可分批
        tone, label = "buy", "现价贴近MA20，可分批"

    numbers = f"现价 {price:.2f}，MA20 {ma20:.2f}"
    if rsi is not None:
        numbers += f"，RSI {rsi:.0f}"
    if volatility is not None:
        numbers += f"，20日波动 {volatility:.1f}%"
    return {"label": label, "tone": tone, "detail": f"{numbers} — {label}"}


def _fetch_closes(session: Session, codes: list[str]) -> dict[str, list[float]]:
    if not codes:
        return {}
    rows = session.execute(
        select(DailyBar.ts_code, DailyBar.close)
        .where(DailyBar.ts_code.in_(codes))
        .order_by(DailyBar.ts_code, DailyBar.trade_date.desc())
    ).all()
    buckets: dict[str, list[float]] = {}
    for code, close in rows:
        bucket = buckets.setdefault(code, [])
        if len(bucket) < MAX_BARS:
            bucket.append(float(close))
    return {code: list(reversed(closes)) for code, closes in buckets.items()}  # 倒序→正序


def compute_timing_for_codes(
    session: Session,
    codes: Iterable[str],
    fresh_price: dict[str, float | None] | None = None,
) -> dict[str, dict[str, str] | None]:
    """为给定代码批量计算操作时机标签。

    price 参考优先取 fresh_price[code]（实时且非 stale 的报价），否则回退到
    最近一根日线收盘——盘后无实时行情时仍能给出基于收盘价的标签。
    """
    fresh_price = fresh_price or {}
    closes = _fetch_closes(session, list(codes))
    result: dict[str, dict[str, str] | None] = {}
    for code, series in closes.items():
        if len(series) < MIN_BARS:
            result[code] = None
            continue
        indicators = calculate_indicators([{"close": c} for c in series])
        price = fresh_price.get(code) or series[-1]
        result[code] = decide_timing(
            price,
            indicators.get("ma5"),
            indicators.get("ma20"),
            indicators.get("rsi"),
            indicators.get("volatility"),
        )
    return result
