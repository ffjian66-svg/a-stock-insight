"""单股买卖计划与持仓建议。

这一层把「序列 → 信号」翻译成「现在该不该买、买多少、什么条件卖」。它只 import
`signals`/`indicators`/`performance`，**不 import `backtest`**：回测结果由 API 层以
`expectancy=` 注入，保持内核纯计算、可离线单测。`expectancy` 的 type 只在
`TYPE_CHECKING` 下引入，运行时不需要它。

## 诚实政策（本模块存在的理由）

每个结论都必须挂着**该规则在该标的上的实测期望与样本量**：

- 入场条件里的 `expectancy_gate` 与动作表共同保证：**实测期望为负的策略永远不会渲染成
  "买入"，无论信号多强**（`_decide_action`）。
- 样本不足时判 `insufficient` 并只给"观望"，不给任何方向的暗示。
- 结论必然附带 `verdict_text`——把"亏钱的规则"直说，而不是用"信号较强"含混过去。
- **全市场汇总只能否决、不能放行**（`_market_veto`）。个股只有几笔成交时，
  "不知道"至少能补上"这条规则在全市场 N 万笔上是什么结果"；但那个数字永远不能把
  `watch`/`avoid` 抬成 `buy`，否则池化就成了绕开个股样本门槛的后门。
- **不设固定止盈目标**：固定目标是一个必须被"选出来"（即拟合）的参数，且会截断唯一有
  微弱依据的收益族的上行。唯一止盈机制是移动止盈，且它是可实测的（`exit_reason == "trail"`）。

## 非目标（写在这里以便范围蔓延可见）

不接券商、不自动下单；不做空/融资/杠杆（A 股多头）；不做盘中或实时信号，全部基于收盘；
不叠基本面与新闻（那是 /quant/factors 与 /screener）；不做参数搜索、不做个股阈值调优、
不做机器学习——理由见 `signals` 的阈值注释（2 年 ~10 笔成交的期望值标准误与均值同量级，
搜索几乎必然找到噪声，而那个数字会被当作"实测历史表现"展示）。

`advise_position`（持仓建议）**刻意不做全市场否决**：否决针对的是"按一条没有市场 edge 的
规则开新仓"，而持仓建议管的是已经拿在手里的仓位，离场机器是止损/移动止盈/分数。一条全市场
统计不该逼人清仓。这是有意的口径差异，不是漏掉。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Optional

from app.services.quant.indicators import indicator_series
from app.services.quant.performance import parse_date
from app.services.quant.signals import (
    ATR_STOP_MULTIPLE,
    BUY_SCORE,
    FUSION_BUY_SCORE,
    FUSION_SELL_SCORE,
    MIN_CONFIRM_RULES,
    SELL_SCORE,
    FamilyScore,
    Signal,
    evaluate_signals,
    fused_action,
    fusion_score,
    score_families,
)
from app.services.timing import EXTENDED_DEV

if TYPE_CHECKING:  # pragma: no cover - 仅为类型标注，运行时不导入（见模块 docstring）
    from app.services.quant.backtest import Expectancy

__all__ = [
    "FALLBACK_STOP_PCT",
    "Holding",
    "LedgerEntry",
    "MarketEdge",
    "Plan",
    "PositionAdvice",
    "RISK_PCT",
    "advise_position",
    "build_plan",
    "summarize_ledger",
]

# —— 显式常量：全部先验选定，没有任何一个是搜出来的 ——
RISK_PCT = 0.01  # 单笔风险预算占本金比例
LOT = 100  # A 股一手
FALLBACK_STOP_PCT = 0.92  # 结构位与 ATR 位都不可用时的兜底止损（现价 -8%）
LIMIT_DOWN_NOTE_PCT = -9.5  # 超过这个跌幅就可能触及主板跌停，跳空时无法保证成交
TRAIL_BARS_LOOKBACK = 10  # 未建仓时用最近 N 根的最高价估算移动止盈水位

# 波动率分档 → 单标的最大仓位。波动越大，同样的仓位在 A 股 T+1 下越危险。
_VOL_BANDS: tuple[tuple[float, float], ...] = ((35.0, 0.20), (55.0, 0.15))
_VOL_CAP_FLOOR = 0.10

# 入场价区的 ATR 几何：按策略所属族偏移，**没有拟合参数**。
# 趋势跟随要等在回撤里买、不追高；均值回归要等更深的偏离；融合取两者之间。
_ZONE_ATR: dict[str, tuple[float, float]] = {
    "trend_follow": (0.5, 0.25),
    "ma_cross": (0.5, 0.25),
    "macd_cross": (0.5, 0.25),
    "trend_combo": (0.5, 0.25),
    "donchian": (0.5, 0.25),
    "timing": (0.5, 0.25),
    "mean_reversion": (1.0, 0.2),
    "kdj_cross": (1.0, 0.2),
    "boll_reversion": (1.0, 0.2),
    "fusion": (0.75, 0.25),
    "buy_hold": (0.75, 0.25),
}
_ZONE_DEFAULT = (0.75, 0.25)

# 样本不足时的经验持有期（交易日）。明确标注为经验值，绝不当成实测值展示。
_HOLD_HINTS: dict[str, tuple[int, int]] = {
    "trend_follow": (20, 60),
    "mean_reversion": (3, 15),
    "fusion": (10, 40),
}
_HOLD_HINT_DEFAULT = (10, 40)

_STOP_SOURCE_LABELS = {
    "atr": "ATR 位",
    "donchian": "20 日结构低点",
    "fallback": "兜底",
}

_ACTION_LABELS = {
    "buy": "信号与实测均支持买入（仍请自行判断）",
    "watch": "继续观望",
    "avoid": "不建议买入",
}

_POSITION_ACTIONS = ("exit", "reduce", "hold", "add", "watch")

_MIN_BARS = 21  # 少于此无法给出有意义的计划（MA20 与 ATR14 都还没预热完）

_VERDICT_LABELS = {"positive": "正期望", "negative": "负期望", "insufficient": "样本不足"}


def _fmt_pct(value: Optional[float]) -> str:
    """`expectancy_pct` 在零成交时是 None——不能直接格式化，也不能显示成 0.00%。"""
    return "无成交" if value is None else f"{value:+.2f}%"


# ======================================================================================
# 结果类型
# ======================================================================================
@dataclass(frozen=True)
class PlanCondition:
    key: str
    label: str
    satisfied: bool
    detail: str


@dataclass(frozen=True)
class PriceZone:
    low: float
    high: float
    reference: float
    source: str
    note: str


@dataclass(frozen=True)
class StopPlan:
    atr_level: Optional[float]
    structure_level: Optional[float]
    recommended: float
    distance_pct: float
    source: str
    note: str


@dataclass(frozen=True)
class TrailPlan:
    level: Optional[float]
    high_water: float
    note: str


@dataclass(frozen=True)
class PositionSizing:
    shares: int
    lots: int
    amount: float
    weight_pct: float
    risk_amount: float
    risk_pct: float
    affordable: bool
    formula: str
    note: str


@dataclass(frozen=True)
class HoldEstimate:
    basis: str  # "measured" | "heuristic"
    bars: Optional[int]
    low: int
    high: int
    note: str


@dataclass(frozen=True)
class ExitRule:
    key: str
    label: str
    triggered: bool
    detail: str


@dataclass(frozen=True)
class ScorePoint:
    trade_date: date
    trend: float
    reversion: float
    fusion: float


@dataclass(frozen=True)
class FamilyScoreView:
    name: str
    label: str
    score: float
    buy_threshold: float
    sell_threshold: float
    buy_rules: tuple[str, ...]
    sell_rules: tuple[str, ...]
    direction: str


@dataclass(frozen=True)
class FamilyScores:
    trend: FamilyScoreView
    reversion: FamilyScoreView
    fusion: FamilyScoreView


@dataclass(frozen=True)
class MarketEdge:
    """一条规则在**全市场**的实测记录——与 `Expectancy` 是两回事，绝不可互相顶替。

    `Expectancy` 说的是"这条规则在这只股票上做了 n 笔"，本类说的是"这条规则在全市场
    做了 N 笔"。`verdict`/`excess_per_bar_pct` 都由调用方（`quant.pool`）算好后注入，
    理由与 `expectancy=` 一样：本模块不 import `backtest`，保持内核纯计算、可离线单测。

    **只否决、不放行**是本类存在的契约（见 `_market_veto`）。
    """

    strategy: str
    verdict: str  # "positive" | "negative" | "insufficient"，由 backtest.verdict_for 判定
    trade_count: int
    expectancy_pct: Optional[float]
    expectancy_per_bar_pct: Optional[float]
    excess_per_bar_pct: Optional[float]  # 已扣掉池化 buy_hold 的每 bar 期望
    stocks_with_trades: int
    bars_median: int
    computed_at: Optional[datetime]
    rule_version: str
    # 基准行（买入持有）不参与否决：它不宣称任何 edge，池化基准为负说的是"这段行情在跌"，
    # 那是行情判断而不是规则质量判断。名字与 `schemas.ExpectancyRow.is_baseline` 对齐。
    is_baseline: bool = False


@dataclass(frozen=True)
class Plan:
    """单股买卖计划。字段与 `schemas.BuySellPlan` 1:1，便于 `asdict` 直通 pydantic。"""

    ts_code: str
    name: str
    industry: str
    strategy: str
    strategy_label: str
    as_of: Optional[date]
    bars: int
    price: float
    price_source: str
    is_stale: bool
    action: str
    action_label: str
    reasons: tuple[str, ...]
    entry_conditions: tuple[PlanCondition, ...]
    entry_zone: PriceZone
    stop_loss: StopPlan
    take_profit: TrailPlan
    sizing: PositionSizing
    expected_hold: HoldEstimate
    exit_rules: tuple[ExitRule, ...]
    scores: FamilyScores
    score_history: tuple[ScorePoint, ...]
    honesty: Expectancy  # 只作类型标注；运行时不导入 backtest，见模块 docstring


@dataclass(frozen=True)
class LedgerEntry:
    """一笔流水。派生值（加权成本/已实现盈亏）一律实时重算，不落库、不会变陈旧。"""

    side: str  # "buy" | "sell"
    trade_date: date
    price: float
    shares: float
    fee: float = 0.0


@dataclass(frozen=True)
class Holding:
    shares: float
    avg_cost: float
    cost: float
    realized_pnl: float
    first_entry_date: Optional[date]
    trade_count: int


@dataclass(frozen=True)
class PositionAdvice:
    ts_code: str
    name: str
    industry: str
    status: str  # "holding" | "closed"
    shares: float
    avg_cost: float
    cost: float
    price: Optional[float]
    price_source: str
    is_stale: bool
    pnl: Optional[float]
    pnl_pct: Optional[float]
    realized_pnl: float
    hold_bars: int
    first_entry_date: Optional[date]
    trade_count: int  # 该标的的流水笔数，持仓行直接显示"n 笔"，不必再请求明细
    stop_price: Optional[float]
    stop_source: str  # "rule" | "user" | "none"
    stop_distance_pct: Optional[float]
    stop_triggered: bool
    trail_level: Optional[float]
    trail_triggered: bool
    scores: Optional[FamilyScores]
    action: str
    action_label: str
    reasons: tuple[str, ...]
    data_warning: str


# ======================================================================================
# 流水账 → 持仓（纯函数，按日期顺序回放）
# ======================================================================================
def summarize_ledger(trades: Sequence[LedgerEntry]) -> Optional[Holding]:
    """把流水账折成持仓。**卖出不改均价、只减股数**（券商口径）。

    `avg_cost` 是剩余持仓的加权平均买入成本（含买入手续费），所以
    `realized_pnl = (卖价 - 当时均价) × 股数 - 卖出手续费` 累加得到。
    没有任何流水时返回 None；已清仓时返回 `shares == 0` 的 Holding（已实现盈亏仍可见）。

    **它会把超卖夹到 0，所以不是校验器**：往一个空仓里卖 500 股会得到 `shares == 0` 而
    不是 −500。超卖必须在**写入时**检查（`strategy_routes._oversell_date`），显示层这里
    夹住是对的——"持有 0 股"比"持有 −500 股"更接近事实。
    """
    ordered = sorted(trades, key=lambda item: (item.trade_date, item.side != "buy"))
    if not ordered:
        return None
    shares = 0.0
    cost_basis = 0.0
    realized = 0.0
    first_entry: Optional[date] = None
    for item in ordered:
        if item.side == "buy":
            shares += item.shares
            cost_basis += item.shares * item.price + item.fee
            if first_entry is None:
                first_entry = item.trade_date
            continue
        if shares <= 0:
            continue  # 超卖已在 API 层拦下；这里只做防御，不让账面变成负数
        sold = min(item.shares, shares)
        average = cost_basis / shares
        realized += (item.price - average) * sold - item.fee
        cost_basis -= average * sold
        shares -= sold
    return Holding(
        shares=shares,
        avg_cost=(cost_basis / shares) if shares > 0 else 0.0,
        cost=cost_basis,
        realized_pnl=realized,
        first_entry_date=first_entry,
        trade_count=len(ordered),
    )


# ======================================================================================
# 共享内核：分数 / 止损 / 移动止盈 / 失效条件
# ======================================================================================
def _view(name: str, label: str, family: FamilyScore) -> FamilyScoreView:
    return FamilyScoreView(
        name=name,
        label=label,
        score=round(family.score, 1),
        buy_threshold=BUY_SCORE,
        sell_threshold=SELL_SCORE,
        buy_rules=tuple(family.buy_rules),
        sell_rules=tuple(family.sell_rules),
        direction=family.direction,
    )


def _family_scores(signals: Sequence[Signal]) -> FamilyScores:
    raw = score_families(signals)
    trend, reversion = raw["trend"], raw["reversion"]
    fused = fusion_score([trend, reversion])
    return FamilyScores(
        trend=_view("trend", "趋势跟踪族", trend),
        reversion=_view("reversion", "均值回归族", reversion),
        fusion=FamilyScoreView(
            name="fusion",
            label="融合分",
            score=round(fused, 1),
            buy_threshold=FUSION_BUY_SCORE,
            sell_threshold=FUSION_SELL_SCORE,
            buy_rules=tuple(
                dict.fromkeys([*trend.buy_rules, *reversion.buy_rules])
            ),
            sell_rules=tuple(
                dict.fromkeys([*trend.sell_rules, *reversion.sell_rules])
            ),
            direction=fused_action("fusion", signals),
        ),
    )


def _at(series: dict[str, list[float | None]], key: str, index: int) -> Optional[float]:
    values = series.get(key)
    if values is None or index < 0 or index >= len(values):
        return None
    value = values[index]
    if value is None or not math.isfinite(value):
        return None
    return value


def _stop_plan(price: float, atr: Optional[float], structure: Optional[float]) -> StopPlan:
    """止损位 = ATR 位与 20 日结构低点里**更近**的那条。

    ATR 位复用 `signals.ATR_STOP_MULTIPLE`——与 `atr_stop` 信号、与回测的
    `stop_loss_atr` 完全同源，页面上显示的止损就是引擎实际执行的止损。
    取更近的一条：A 股 T+1 且无盘中保护，止损越宽越容易被跳空穿透；而风险预算按
    `入场 - 止损` 计算，**止损收紧会自动缩小仓位，而不是放大损失**。
    """
    atr_level = round(price - ATR_STOP_MULTIPLE * atr, 2) if atr else None
    structure_level = round(structure, 2) if structure else None

    candidates = [value for value in (atr_level, structure_level) if value is not None]
    notes: list[str] = []
    if atr_level is not None and structure_level is not None:
        recommended = max(candidates)
        source = "atr" if atr_level >= structure_level else "donchian"
    elif candidates:
        recommended = candidates[0]
        source = "atr" if atr_level is not None else "donchian"
    else:
        recommended = 0.0
        source = "none"

    if not candidates:
        notes.append("ATR 与 20 日结构位都不可用")
    if recommended <= 0 or recommended >= price:
        notes.append("结构位与 ATR 位缺失或高于现价")
        recommended = round(price * FALLBACK_STOP_PCT, 2)
        source = "fallback"
        notes.append(f"退回按现价 -{(1 - FALLBACK_STOP_PCT) * 100:.0f}% 的兜底止损")

    if atr_level is not None and structure_level is not None and source != "fallback":
        notes.append(
            f"ATR 位 {atr_level:.2f}、20 日结构低点 {structure_level:.2f}，取更近的一条"
            "（止损越宽越容易被跳空穿透；收紧止损会自动缩小下面的建议仓位）"
        )

    distance_pct = round((recommended / price - 1) * 100, 2)
    if distance_pct < LIMIT_DOWN_NOTE_PCT:
        notes.append(
            f"跌幅 {distance_pct:.1f}% 已超过主板 ±10% 的日内限制，"
            "跳空时可能无法在该价位成交"
        )
    if not notes:
        notes.append("ATR 与结构位同源计算，页面数字与回测执行的止损逐位一致")
    return StopPlan(
        atr_level=atr_level,
        structure_level=structure_level,
        recommended=recommended,
        distance_pct=distance_pct,
        source=source,
        note="；".join(notes),
    )


def _trail_plan(
    price: float,
    atr: Optional[float],
    highs: Sequence[float],
    *,
    high_water: Optional[float] = None,
) -> TrailPlan:
    """移动止盈（吊灯止损）= 最高价 - 2×ATR。**这是唯一的止盈机制**，见模块 docstring。"""
    if high_water is None:
        recent = list(highs[-TRAIL_BARS_LOOKBACK:]) if highs else []
        high_water = max([*recent, price]) if recent else price
        note = (
            f"未建仓：暂用最近 {len(recent)} 根的最高价 {high_water:.2f} 估算，建仓后会按实际"
            "持仓期最高价重算"
        )
    else:
        note = "已持仓：按建仓以来的实际最高价计算"
    if not atr:
        return TrailPlan(
            level=None,
            high_water=round(high_water, 2),
            note=f"{note}；ATR 缺失，无法给出止盈位",
        )
    level = round(high_water - ATR_STOP_MULTIPLE * atr, 2)
    return TrailPlan(
        level=level,
        high_water=round(high_water, 2),
        note=(
            f"{note}；自最高价回落 {ATR_STOP_MULTIPLE:.0f}×ATR({atr:.2f}) 即出场。"
            "刻意不设固定止盈目标——固定目标必须被「选」出来，且会截断趋势的上行"
        ),
    )


def _annual_vol_pct(series: dict[str, list[float | None]], index: int) -> Optional[float]:
    """20 日年化波动率（%）。`series["volatility"]` 是日收益标准差×100，未年化。"""
    daily = _at(series, "volatility", index)
    return None if daily is None else daily * math.sqrt(252)


def _position_cap(annual_vol: Optional[float]) -> float:
    if annual_vol is None:
        return _VOL_CAP_FLOOR
    for threshold, cap in _VOL_BANDS:
        if annual_vol <= threshold:
            return cap
    return _VOL_CAP_FLOOR


def _sizing(
    price: float,
    stop: float,
    equity: float,
    fused: float,
    annual_vol: Optional[float],
    *,
    risk_pct: float = RISK_PCT,
) -> PositionSizing:
    """三步定仓：风险预算 → 波动上限 → 置信度折扣。每一步都是显式常量，无拟合参数。"""
    risk_per_share = price - stop
    cap = _position_cap(annual_vol)
    if risk_per_share <= 0:
        shares_risk = 0
    else:
        shares_risk = int(equity * risk_pct / risk_per_share // LOT) * LOT
    shares_vol = int(equity * cap / price // LOT) * LOT

    # 置信度折扣：融合分 50 → 0.4 倍，50 → 75 线性升到 1.0。分数越低仓位越小，
    # 这是"证据薄弱时自动少下注"，不是择时优化。
    factor = 0.4 + 0.6 * min(max((fused - 50.0) / 25.0, 0.0), 1.0)
    base = min(shares_risk, shares_vol)
    shares = int(base * factor // LOT) * LOT

    amount = round(shares * price, 2)
    risk_amount = round(shares * max(risk_per_share, 0.0), 2)
    vol_text = f"{annual_vol:.1f}%" if annual_vol is not None else "缺失，按最低档"
    formula = (
        f"① 风险预算：floor({equity:,.0f}×{risk_pct:.0%} ÷ ({price:.2f}-{stop:.2f}) ÷ {LOT})"
        f"×{LOT} = {shares_risk:,} 股  "
        f"② 波动上限：floor({equity:,.0f}×{cap:.0%} ÷ {price:.2f} ÷ {LOT})×{LOT} "
        f"= {shares_vol:,} 股（20 日年化波动 {vol_text}）  "
        f"③ 置信度折扣：min(①②)×{factor:.2f}（融合分 {fused:.1f}）"
        f" 取整手 = {shares:,} 股"
    )

    if shares <= 0:
        # **三条不同**的原因都会让股数取到 0，文案必须分开说。混在一起会写出可验证的假话：
        # 高分档本金、止损离得远时，真正的约束是"一手股票的风险就超过 1% 预算"，账户
        # 完全买得起（茅台 1258 元 / 止损 1157 元的真实计划就是这样，写成"买不起"会被
        # 用户一句话推翻）；而分数太低把折扣归零时，①②明明给得出股数。
        lot_cost = price * LOT
        lot_risk = max(risk_per_share, 0.0) * LOT
        if risk_per_share <= 0:
            note = (
                f"建议止损 {stop:.2f} 不低于入场参考价 {price:.2f}，风险预算无从计算，"
                "因此不给股数。止损在入场价之上时买多少都是错的，请先修正止损位。"
            )
        elif shares_vol <= 0:
            # 先判这条：波动上限下的可投金额连一手都买不到时，"买不起"是压倒性的事实，
            # 此时再说"风险预算不允许"反而把真实障碍说小了。
            note = (
                f"按波动上限，可取整手为 0：一手需 {lot_cost:,.0f} 元"
                f"（现价 {price:.2f} × {LOT} 股），账户 {equity:,.0f} 元、"
                f"该档仓位上限 {cap:.0%} 对应 {equity * cap:,.0f} 元。"
                "这不是「没有信号」，是「这套风险纪律下买不起」——请调高本金或换标的。"
            )
        elif shares_risk <= 0:
            note = (
                f"按 {risk_pct:.0%} 风险预算：一手 {LOT} 股在 {price:.2f} 入场、止损 {stop:.2f} 下"
                f"的风险是 {lot_risk:,.0f} 元，已超过预算 {equity * risk_pct:,.0f} 元"
                f"（本金 {equity:,.0f} 元），所以取整手为 0。"
                "账户买得起这一手，是这套风险纪律不允许——请调高本金，或等一个止损更近的入场点；"
                "把止损放宽来凑股数只会把单笔风险做大，不是解决。"
            )
        else:
            need = LOT / base if base else 1.0
            need_score = 50.0 + 25.0 * (need - 0.4) / 0.6
            note = (
                f"①②允许 {base:,} 股，但融合分 {fused:.1f} 的置信度折扣只有 {factor:.2f} 倍，"
                f"取整手后为 0（要拿到一手 {LOT} 股，折扣需 ≥ {need:.2f}，"
                f"约对应融合分 ≥ {min(max(need_score, 0.0), 100.0):.0f}）。"
                "账户买得起，是证据不足以支撑一笔仓位。"
            )
        return PositionSizing(
            shares=0,
            lots=0,
            amount=0.0,
            weight_pct=0.0,
            risk_amount=0.0,
            risk_pct=risk_pct,
            affordable=False,
            formula=formula,
            note=note,
        )

    note = (
        f"占本金 {amount / equity * 100:.1f}%；若在 {price:.2f} 附近成交并触发止损，"
        f"单笔亏损约 {risk_amount:,.0f} 元（≈本金的 {risk_amount / equity * 100:.2f}%）。"
        "公式全部印在上面，可手算复核"
    )
    return PositionSizing(
        shares=shares,
        lots=shares // LOT,
        amount=amount,
        weight_pct=round(amount / equity * 100, 2),
        risk_amount=risk_amount,
        risk_pct=risk_pct,
        affordable=True,
        formula=formula,
        note=note,
    )


def _exit_rules(
    *,
    price: float,
    stop: float,
    atr: Optional[float],
    trail: TrailPlan,
    fused: float,
    trend_score: float,
    series: dict[str, list[float | None]],
    index: int,
    avg_hold_bars: Optional[float],
    hold_bars: int,
    pnl_pct: Optional[float],
) -> tuple[ExitRule, ...]:
    """5 条失效/清仓条件，各带实时 `triggered`。计划与持仓建议共用同一份。"""
    ma5 = _at(series, "ma5", index)
    ma20 = _at(series, "ma20", index)
    cross_down = ma5 is not None and ma20 is not None and ma5 < ma20

    time_limit = (
        max(round(2 * avg_hold_bars), 1)
        if avg_hold_bars
        else _HOLD_HINT_DEFAULT[1]
    )
    trail_hit = (
        trail.level is not None and price <= trail.level
    ) or (
        atr is not None and trail.high_water > price
        and (trail.high_water - price) >= ATR_STOP_MULTIPLE * atr
    )
    return (
        ExitRule(
            key="fusion_exit",
            label=f"融合分跌破 {FUSION_SELL_SCORE:.0f}",
            triggered=fused <= FUSION_SELL_SCORE,
            detail=f"当前融合分 {fused:.1f}（卖出线 {FUSION_SELL_SCORE:.0f}）",
        ),
        ExitRule(
            key="stop_hit",
            label="收盘跌破止损位",
            triggered=price <= stop,
            detail=f"现价 {price:.2f}，止损位 {stop:.2f}",
        ),
        ExitRule(
            key="trail_hit",
            label=f"自最高价回落 {ATR_STOP_MULTIPLE:.0f}×ATR",
            triggered=bool(trail_hit),
            detail=(
                f"持仓期最高 {trail.high_water:.2f}，现价 {price:.2f}"
                + (
                    f"，回撤阈值 {(trail.high_water - price):.2f}"
                    if trail.high_water > price
                    else ""
                )
            ),
        ),
        ExitRule(
            key="trend_break",
            label="MA5 下穿 MA20 且趋势分走弱",
            triggered=bool(cross_down and trend_score < 50.0),
            detail=(
                f"MA5 {ma5:.2f} / MA20 {ma20:.2f}，趋势分 {trend_score:.1f}"
                if ma5 is not None and ma20 is not None
                else "均线数据不足"
            ),
        ),
        ExitRule(
            key="time_stop",
            label=f"持有超过 {time_limit} 个交易日仍浮亏",
            triggered=bool(hold_bars >= time_limit and pnl_pct is not None and pnl_pct < 0),
            detail=(
                f"已持有 {hold_bars} 个交易日"
                + (f"，浮亏 {pnl_pct:.2f}%" if pnl_pct is not None else "（未持仓，不适用）")
                + (
                    f"；阈值取自实测平均持有 {avg_hold_bars:.0f} bar × 2"
                    if avg_hold_bars
                    else "；阈值取自经验值"
                )
            ),
        ),
    )


# ======================================================================================
# 买卖计划
# ======================================================================================
def _weak_confirm(scores: FamilyScores) -> bool:
    """分数够线但只有一条规则确认——`family_gate` 必须把这件事说出来。

    否则用户看到「回归 71.4 分却仍不能买」时无从判断是哪一条没过（族分够 65，但
    `MIN_CONFIRM_RULES` 要求两条不同规则同向）。
    """
    topped = max(scores.trend.score, scores.reversion.score) >= BUY_SCORE
    confirmed = max(len(scores.trend.buy_rules), len(scores.reversion.buy_rules))
    return topped and confirmed < MIN_CONFIRM_RULES


def _market_veto(expectancy: Expectancy, market_edge: Optional[MarketEdge]) -> Optional[str]:
    """全市场汇总能否否决这次买入；能则返回降级后的动作文案，否则 `None`。

    **两个条件必须同时成立**：规则在全市场①既不赚钱（`verdict == "negative"`），
    ②又跑不赢等权买入持有（每 bar 超额为负）。只看①会把"在市场跌 12% 时打平"的规则
    当成没有 edge——`backtest.py` 把那种读法直接称为"反向撒谎"。这是两个符号的合取，
    不是新阈值，也不需要选参数。

    三条保守约束（都是"宁可不妨"的方向）：
    - `market_edge is None`（汇总表还没算过）→ 不否决，行为与加本功能之前逐位一致；
    - `is_baseline`（买入持有本身）→ 不否决，见 `MarketEdge` 的说明；
    - 策略名对不上 → 不否决，绝不能拿另一条规则的战绩去否这只股票。
    """
    if market_edge is None or market_edge.is_baseline:
        return None
    if market_edge.strategy != expectancy.strategy:
        return None
    if market_edge.verdict != "negative":
        return None
    if market_edge.excess_per_bar_pct is None or market_edge.excess_per_bar_pct > 0:
        return None
    return (
        f"信号与个股实测都支持买入，但「{expectancy.strategy_label}」在全市场"
        f"{market_edge.trade_count:,} 笔（{market_edge.stocks_with_trades:,} 只）上"
        f"每笔期望 {_fmt_pct(market_edge.expectancy_pct)}、"
        f"扣掉同期等权买入持有后每 bar {_fmt_pct(market_edge.excess_per_bar_pct)}，"
        "两项都为负——该规则没有全市场 edge，故不给买入结论"
    )


def _decide_action(
    conditions: Sequence[PlanCondition],
    expectancy: Expectancy,
    market_edge: Optional[MarketEdge] = None,
) -> tuple[str, str]:
    """动作决策表——**这就是诚实政策，写成代码**。

    `expectancy_gate` 不计入"信号侧条件"：它由下面的 verdict 分支单独处理，这样"信号全
    满足但实测为负"才能落到 `avoid`（而不是因为一条条件没过就笼统地"观望"，那会掩盖
    "这条规则在这只股票上是亏钱的"这个真正重要的结论）。

    全市场否决**必须放在这里**、作为 `buy` 分支的后置覆盖，而不能写进 `entry_conditions`：
    写进去就会毁掉上一段刚说的性质（"信号全满足但个股实测为负"会退化成含混的观望），
    而且前端会把新增的那条渲染成一个绿色的 ✓（`StrategyPage.tsx` 的入场条件表），
    那等于变相「放行」——恰恰是本功能的契约不允许的方向。
    """
    signal_side = [item for item in conditions if item.key != "expectancy_gate"]
    if any(not item.satisfied for item in signal_side):
        return "watch", "未满足入场条件，继续等待"
    if expectancy.verdict == "positive":
        veto = _market_veto(expectancy, market_edge)
        if veto is not None:
            return "watch", veto
        return "buy", _ACTION_LABELS["buy"]
    if expectancy.verdict == "insufficient":
        return (
            "watch",
            f"信号已触发，但该规则在此标的只有 {expectancy.trade_count} 笔成交，"
            "样本不足以证明有效",
        )
    return (
        "avoid",
        f"信号已触发，但该规则在此标的实测每笔期望 {_fmt_pct(expectancy.expectancy_pct)}，"
        "不建议据此买入",
    )


def build_plan(
    bars: Sequence[dict[str, Any]],
    *,
    ts_code: str,
    name: str = "",
    industry: str = "",
    strategy: str = "fusion",
    equity: float = 1_000_000.0,
    risk_pct: float = RISK_PCT,
    expectancy: Expectancy,
    market_edge: Optional[MarketEdge] = None,
    price: Optional[float] = None,
    price_source: str = "close",
    is_stale: bool = False,
    score_history_bars: int = 30,
) -> Plan:
    """构建单股买卖计划。`bars` 为按日期正序的日线行，`expectancy` 由调用方注入。

    策略展示名直接取自 `expectancy.strategy_label`——调用方保证两者说的是同一个策略，
    这样本模块不必 import `backtest`（见模块 docstring 的分层说明）。

    `market_edge` 是**可选**的全市场汇总（`quant.pool` 算好后注入）。它只做两件事：
    ①补充 `expectancy_gate` 的样本量文案；②在下列全部成立时把 `buy` 降级为 `watch`——
    个股实测为正、四条信号条件全过、且该规则全市场既不赚钱又跑不赢买入持有。
    **它永远不会把 `watch`/`avoid` 变成 `buy`。** 不传时行为与加本参数之前逐位一致。
    """
    label = expectancy.strategy_label
    rows = [row for row in bars if row.get("close") is not None]
    series = indicator_series(rows)
    closes = [float(row["close"]) for row in rows]
    dates = [parse_date(row.get("trade_date") or row.get("date")) for row in rows]
    index = len(closes) - 1
    last_price = float(price) if price else closes[index]
    atr = _at(series, "atr14", index)
    ma20 = _at(series, "ma20", index)

    signals = evaluate_signals(series, closes, index)
    scores = _family_scores(signals)
    trend_score = scores.trend.score
    fused = scores.fusion.score

    stop = _stop_plan(last_price, atr, _at(series, "donchian_low", index))
    trail = _trail_plan(last_price, atr, [float(row["high"]) for row in rows if row.get("high")])

    below, above = _ZONE_ATR.get(strategy, _ZONE_DEFAULT)
    zone_low = last_price - below * atr if atr else last_price
    zone_high = last_price + above * atr if atr else last_price
    # 绝不允许把入场价定在止损之下——那等于"一买就触发止损"。
    zone_low = max(zone_low, stop.recommended)
    zone_low, zone_high = round(zone_low, 2), round(zone_high, 2)
    if zone_low > zone_high:
        zone_low = zone_high  # ATR 极小时可能发生，夹紧而不是断言失败
    entry_zone = PriceZone(
        low=zone_low,
        high=zone_high,
        reference=round(last_price, 2),
        source=price_source,
        note=(
            f"按「{label}」的 ATR 几何给出（现价 {last_price:.2f}，"
            f"ATR14 {atr:.2f}）；挂单未成交属正常情形，不要在区间外追价"
            if atr
            else "ATR 缺失，退化为现价单点"
        ),
    )

    # 入场条件：固定 5 条，永远全给，带 satisfied 与 detail。用户能看到"71 分却仍观望"
    # 究竟是哪一条没过——这是把门槛摊开给人看，而不是藏在结论里。
    gap = round(FUSION_BUY_SCORE - fused, 1)
    conditions = [
        PlanCondition(
            key="score_gate",
            label=f"融合分 ≥ {FUSION_BUY_SCORE:.0f}",
            satisfied=fused >= FUSION_BUY_SCORE,
            detail=(
                f"当前 {fused:.1f}，差 {gap:.1f}"
                if gap > 0
                else f"当前 {fused:.1f}，已达标"
            ),
        ),
        PlanCondition(
            key="family_gate",
            label=f"至少一个族的分数 ≥ {BUY_SCORE:.0f} 且 ≥ {MIN_CONFIRM_RULES} 条规则同向",
            satisfied=scores.trend.direction == "buy" or scores.reversion.direction == "buy",
            detail=(
                f"趋势 {scores.trend.score:.1f}（{len(scores.trend.buy_rules)} 条确认）/ "
                f"回归 {scores.reversion.score:.1f}（{len(scores.reversion.buy_rules)} 条确认）"
                + ("；分数够线但只有一条规则确认，不算数" if _weak_confirm(scores) else "")
            ),
        ),
        PlanCondition(
            key="not_extended",
            label=f"现价未追高（≤ MA20 × {1 + EXTENDED_DEV:.2f}）",
            satisfied=bool(ma20 and last_price <= ma20 * (1 + EXTENDED_DEV)),
            detail=(
                f"现价 {last_price:.2f} / MA20 {ma20:.2f}"
                f"（偏离 {last_price / ma20 - 1:+.1%}）"
                if ma20
                else "MA20 数据不足"
            ),
        ),
        PlanCondition(
            key="above_stop",
            label="现价高于建议止损",
            satisfied=last_price > stop.recommended,
            detail=(
                f"现价 {last_price:.2f}，止损 {stop.recommended:.2f}"
                f"（{stop.distance_pct:+.2f}%）"
            ),
        ),
        PlanCondition(
            key="expectancy_gate",
            label="该规则在此标的的实测期望为正且样本足够",
            satisfied=expectancy.verdict == "positive",
            detail=(
                f"{expectancy.trade_count} 笔成交，每笔期望 "
                f"{_fmt_pct(expectancy.expectancy_pct)}，实测结论："
                f"{_VERDICT_LABELS.get(expectancy.verdict, expectancy.verdict)}"
            )
            + _pool_detail(market_edge),
        ),
    ]
    action, action_label = _decide_action(conditions, expectancy, market_edge)

    annual_vol = _annual_vol_pct(series, index)
    sizing = _sizing(last_price, stop.recommended, equity, fused, annual_vol, risk_pct=risk_pct)

    # 绑到局部变量，mypy 才能把 Optional[float] 收窄——`and` 里的收窄传不出来
    avg_hold = expectancy.avg_hold_bars
    measured = expectancy.verdict != "insufficient" and bool(avg_hold)
    if measured and avg_hold:
        bars_hold = int(round(avg_hold))
        low = high = bars_hold
        hold = HoldEstimate(
            basis="measured",
            bars=bars_hold,
            low=low,
            high=high,
            note=(
                f"取自本标的实测平均持有 {avg_hold:.1f} bar"
                f"（{expectancy.trade_count} 笔样本）"
            ),
        )
    else:
        low, high = _HOLD_HINTS.get(strategy, _HOLD_HINT_DEFAULT)
        hold = HoldEstimate(
            basis="heuristic",
            bars=None,
            low=low,
            high=high,
            note=(
                f"经验值（{low}-{high} 个交易日），不是本标的的实测结果——"
                f"该策略在此标的只有 {expectancy.trade_count} 笔成交，不足以统计持有期"
            ),
        )

    exit_rules = _exit_rules(
        price=last_price,
        stop=stop.recommended,
        atr=atr,
        trail=trail,
        fused=fused,
        trend_score=trend_score,
        series=series,
        index=index,
        avg_hold_bars=avg_hold,
        hold_bars=0,
        pnl_pct=None,
    )

    start = max(0, len(closes) - max(1, score_history_bars))
    history: list[ScorePoint] = []
    for offset in range(start, len(closes)):
        point_signals = evaluate_signals(series, closes, offset)
        raw = score_families(point_signals)
        day = dates[offset]
        if day is None:
            continue
        history.append(
            ScorePoint(
                trade_date=day,
                trend=round(raw["trend"].score, 1),
                reversion=round(raw["reversion"].score, 1),
                fusion=round(fusion_score([raw["trend"], raw["reversion"]]), 1),
            )
        )

    reasons = _plan_reasons(
        scores=scores,
        stop=stop,
        sizing=sizing,
        hold=hold,
        expectancy=expectancy,
        action=action,
        market_edge=market_edge,
    )
    return Plan(
        ts_code=ts_code,
        name=name,
        industry=industry,
        strategy=strategy,
        strategy_label=label,
        as_of=dates[index] if dates else None,
        bars=len(rows),
        price=round(last_price, 2),
        price_source=price_source,
        is_stale=is_stale,
        action=action,
        action_label=action_label,
        reasons=reasons,
        entry_conditions=tuple(conditions),
        entry_zone=entry_zone,
        stop_loss=stop,
        take_profit=trail,
        sizing=sizing,
        expected_hold=hold,
        exit_rules=exit_rules,
        scores=scores,
        score_history=tuple(history),
        honesty=expectancy,
    )


def _pool_detail(market_edge: Optional[MarketEdge]) -> str:
    """把全市场样本量接到个股实测后面。

    这是本功能最主要的产出：个股只有 3 笔时，`insufficient` 那行本来只说得出一句
    "不知道"。接上全市场之后，"不知道"变成"这条规则在全市场 2 万笔上每笔期望 −0.6%"——
    仍然不构成买入理由，但不再是空白。
    """
    if market_edge is None:
        return ""
    return (
        f"；全市场汇总（{market_edge.trade_count:,} 笔 / "
        f"{market_edge.stocks_with_trades:,} 只 / 中位 {market_edge.bars_median} 个交易日）："
        f"每笔期望 {_fmt_pct(market_edge.expectancy_pct)}，"
        f"扣掉同期等权买入持有后每 bar {_fmt_pct(market_edge.excess_per_bar_pct)}。"
        # 刻意不用 `**粗体**`：这段文字会原样落在页面上，而这一路上没有 markdown 渲染器
        "这不是本标的的预期，只用来判断该规则有没有全市场 edge——"
        "它只能让结论更保守，不会让结论变得更乐观"
    )


def _plan_reasons(
    *,
    scores: FamilyScores,
    stop: StopPlan,
    sizing: PositionSizing,
    hold: HoldEstimate,
    expectancy: Expectancy,
    action: str,
    market_edge: Optional[MarketEdge] = None,
) -> tuple[str, ...]:
    stop_source_label = _STOP_SOURCE_LABELS.get(stop.source, stop.source)
    lines = [
        f"融合分 {scores.fusion.score:.1f}（买入线 {FUSION_BUY_SCORE:.0f}，"
        f"卖出线 {FUSION_SELL_SCORE:.0f}）——趋势族 {scores.trend.score:.1f}，"
        f"回归族 {scores.reversion.score:.1f}",
        f"止损 {stop.recommended:.2f}（{stop.distance_pct:+.2f}%，来源：{stop_source_label}）",
    ]
    if sizing.affordable:
        lines.append(
            f"建议 {sizing.shares:,} 股（{sizing.weight_pct:.1f}% 仓位，"
            f"止损触发时约亏 {sizing.risk_amount:,.0f} 元）"
        )
    else:
        lines.append("按 1% 风险预算与波动上限，可取整手为 0——这套风险纪律下买不起")
    if hold.basis == "measured":
        lines.append(f"预期持有约 {hold.bars} 个交易日（实测均值）")
    else:
        lines.append(f"预期持有 {hold.low}-{hold.high} 个交易日（经验值，非实测）")
    lines.append(f"实测记录：{expectancy.verdict_text}")
    if action == "avoid":
        lines.append("负期望的策略无论信号多强都不给出买入结论——这是本页对用户的契约")
    # 全市场否决生效时把话说全：否决来自另一层，用户必须能把它和"信号没到"区分开。
    # 只在个股实测为正时提这一句——个股实测为负时下面的 veto 也会成立，
    # 但那时结论早就被个股这一层拦下了，说"个股支持买入"就是撒谎。
    if expectancy.verdict == "positive" and _market_veto(expectancy, market_edge) is not None:
        lines.append(
            "本标的的个股实测支持买入，但该规则在全市场没有 edge——即便信号条件全过，"
            "也不给买入结论。全市场汇总只能让结论更保守，永远不会把观望变成买入"
        )
    return tuple(lines)


# ======================================================================================
# 持仓建议
# ======================================================================================
def advise_position(
    bars: Sequence[dict[str, Any]],
    trades: Sequence[LedgerEntry],
    *,
    ts_code: str,
    name: str = "",
    industry: str = "",
    equity: float = 1_000_000.0,
    expectancy: Optional[Expectancy] = None,
    price: Optional[float] = None,
    price_source: str = "close",
    is_stale: bool = False,
    user_stop: Optional[float] = None,
) -> PositionAdvice:
    """对一个已有持仓给出持有/加仓/减仓/清仓建议。首个命中的规则生效。

    日线缺失或不足时不 500、也不编造分数：`scores=None`、`action="watch"`、
    `data_warning` 给出可执行的下一步。
    """
    holding = summarize_ledger(trades)
    rows = [row for row in bars if row.get("close") is not None]

    if holding is None:
        return PositionAdvice(
            ts_code=ts_code,
            name=name,
            industry=industry,
            status="closed",
            shares=0.0,
            avg_cost=0.0,
            cost=0.0,
            price=None,
            price_source=price_source,
            is_stale=is_stale,
            pnl=None,
            pnl_pct=None,
            realized_pnl=0.0,
            hold_bars=0,
            first_entry_date=None,
            trade_count=0,
            stop_price=None,
            stop_source="none",
            stop_distance_pct=None,
            stop_triggered=False,
            trail_level=None,
            trail_triggered=False,
            scores=None,
            action="watch",
            action_label="无持仓",
            reasons=("尚未录入任何流水",),
            data_warning="",
        )

    status = "holding" if holding.shares > 0 else "closed"
    if len(rows) < _MIN_BARS:
        return PositionAdvice(
            ts_code=ts_code,
            name=name,
            industry=industry,
            status=status,
            shares=holding.shares,
            avg_cost=round(holding.avg_cost, 3),
            cost=round(holding.cost, 2),
            price=None,
            price_source=price_source,
            is_stale=is_stale,
            pnl=None,
            pnl_pct=None,
            realized_pnl=round(holding.realized_pnl, 2),
            hold_bars=0,
            first_entry_date=holding.first_entry_date,
            trade_count=holding.trade_count,
            stop_price=user_stop,
            stop_source="user" if user_stop else "none",
            stop_distance_pct=None,
            stop_triggered=False,
            trail_level=None,
            trail_triggered=False,
            scores=None,
            action="watch",
            action_label="数据不足",
            reasons=("本地暂无该标的的日线数据，无法给出建议",),
            data_warning="本地暂无该标的日线，请先同步数据；持仓数字本身不受影响",
        )

    series = indicator_series(rows)
    closes = [float(row["close"]) for row in rows]
    dates = [parse_date(row.get("trade_date") or row.get("date")) for row in rows]
    index = len(closes) - 1
    last_price = float(price) if price else closes[index]
    atr = _at(series, "atr14", index)

    signals = evaluate_signals(series, closes, index)
    scores = _family_scores(signals)
    fused = scores.fusion.score

    rule_stop = _stop_plan(last_price, atr, _at(series, "donchian_low", index))
    # 用户存了止损就优先用用户的：这能让"你的止损已经偏离回测规则"这件事显形。
    if user_stop:
        stop_price = float(user_stop)
        stop_source = "user"
    else:
        stop_price = rule_stop.recommended
        stop_source = "rule"

    highs = [float(row["high"]) for row in rows if row.get("high")]
    entry_index = None
    if holding.first_entry_date is not None:
        for offset, day in enumerate(dates):
            if day is not None and day >= holding.first_entry_date:
                entry_index = offset
                break
    # 已持仓时最高价只取建仓以来的——建仓以前的高点与这笔交易无关。
    held_highs = highs[entry_index:] if entry_index is not None else highs
    high_water = max(held_highs) if held_highs else last_price
    trail = _trail_plan(last_price, atr, highs, high_water=high_water if held_highs else None)

    hold_bars = 0 if entry_index is None else len(closes) - entry_index
    cost = holding.cost
    pnl = round(holding.shares * last_price - cost, 2) if holding.shares > 0 else None
    pnl_pct = (
        round((last_price / holding.avg_cost - 1) * 100, 2)
        if holding.shares > 0 and holding.avg_cost > 0
        else None
    )
    stop_distance = round((stop_price / last_price - 1) * 100, 2) if stop_price else None

    exit_rules = _exit_rules(
        price=last_price,
        stop=stop_price,
        atr=atr,
        trail=trail,
        fused=fused,
        trend_score=scores.trend.score,
        series=series,
        index=index,
        avg_hold_bars=expectancy.avg_hold_bars if expectancy else None,
        hold_bars=hold_bars,
        pnl_pct=pnl_pct,
    )
    triggered = {rule.key: rule for rule in exit_rules}

    weight_pct = (
        holding.shares * last_price / equity * 100 if holding.shares > 0 and equity else 0.0
    )
    action, label, reasons = _advise_action(
        status=status,
        scores=scores,
        fused=fused,
        last_price=last_price,
        stop_price=stop_price,
        stop_source=stop_source,
        trail=trail,
        pnl_pct=pnl_pct,
        hold_bars=hold_bars,
        avg_hold_bars=expectancy.avg_hold_bars if expectancy else None,
        weight_pct=weight_pct,
        realized_pnl=holding.realized_pnl,
        triggered=triggered,
    )

    return PositionAdvice(
        ts_code=ts_code,
        name=name,
        industry=industry,
        status=status,
        shares=holding.shares,
        avg_cost=round(holding.avg_cost, 3),
        cost=round(holding.cost, 2),
        price=round(last_price, 2),
        price_source=price_source,
        is_stale=is_stale,
        pnl=pnl,
        pnl_pct=pnl_pct,
        realized_pnl=round(holding.realized_pnl, 2),
        hold_bars=hold_bars,
        first_entry_date=holding.first_entry_date,
        trade_count=holding.trade_count,
        stop_price=round(stop_price, 2) if stop_price else None,
        stop_source=stop_source,
        stop_distance_pct=stop_distance,
        stop_triggered=triggered["stop_hit"].triggered,
        trail_level=trail.level,
        trail_triggered=triggered["trail_hit"].triggered,
        scores=scores,
        action=action,
        action_label=label,
        reasons=reasons,
        data_warning="",
    )


def _advise_action(
    *,
    status: str,
    scores: FamilyScores,
    fused: float,
    last_price: float,
    stop_price: float,
    stop_source: str,
    trail: TrailPlan,
    pnl_pct: Optional[float],
    hold_bars: int,
    avg_hold_bars: Optional[float],
    weight_pct: float,
    realized_pnl: float,
    triggered: dict[str, ExitRule],
) -> tuple[str, str, tuple[str, ...]]:
    """建议规则表：首个命中生效。顺序即优先级——保护本金的条件永远排在加仓之前。"""
    if status == "closed":
        return (
            "watch",
            "已清仓",
            (f"该标的已无持仓，累计已实现盈亏 {realized_pnl:,.2f} 元",),
        )

    notes = [
        f"融合分 {fused:.1f}（趋势 {scores.trend.score:.1f} / 回归 {scores.reversion.score:.1f}）",
        f"浮盈 {pnl_pct:+.2f}%，持有 {hold_bars} 个交易日，仓位 {weight_pct:.1f}%",
    ]

    if triggered["stop_hit"].triggered:
        return (
            "exit",
            "跌破止损，清仓",
            (*notes, f"现价 {last_price:.2f} 已跌破止损 {stop_price:.2f}（来源：{stop_source}）"),
        )
    if triggered["trail_hit"].triggered:
        drop = trail.high_water - last_price
        return (
            "exit",
            "触发移动止盈，清仓",
            (
                *notes,
                f"自最高 {trail.high_water:.2f} 回落 {drop:.2f}"
                + (f"（止盈位 {trail.level:.2f}）" if trail.level is not None else ""),
            ),
        )
    if fused <= FUSION_SELL_SCORE:
        return (
            "exit",
            "分数转空，清仓",
            (*notes, f"融合分已跌破卖出线 {FUSION_SELL_SCORE:.0f}"),
        )
    if (
        fused >= FUSION_BUY_SCORE
        and (scores.trend.direction == "buy" or scores.reversion.direction == "buy")
        and weight_pct < 25.0
        and (pnl_pct is None or pnl_pct > -3.0)
    ):
        return (
            "add",
            "信号仍强，可考虑加仓",
            (
                *notes,
                f"融合分 {fused:.1f} 达标且族确认，仓位 {weight_pct:.1f}% 低于 25% 上限",
                "加仓同样按 1% 风险预算计算，不要因为「看好」放大单笔风险",
            ),
        )
    if avg_hold_bars and hold_bars >= max(round(2 * avg_hold_bars), 1) and (pnl_pct or 0) < 0:
        return (
            "reduce",
            "时间止损，减仓",
            (
                *notes,
                f"已持有 {hold_bars} 日，超过实测均值 {avg_hold_bars:.0f} bar 的 2 倍仍浮亏",
            ),
        )
    if fused <= 50.0:
        return (
            "reduce",
            "分数走弱，减仓观察",
            (*notes, f"融合分 {fused:.1f} 已落到中性线以下，减半观察"),
        )
    return ("hold", "继续持有", (*notes, "未触发任何失效条件，按既定止损与移动止盈持有"))

