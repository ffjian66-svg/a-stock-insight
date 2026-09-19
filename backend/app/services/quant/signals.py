"""择时信号层：把指标序列翻译成可读信号。

纯函数，UI 面板与回测引擎消费**同一套** `evaluate_signals`，避免"展示的规则"
和"回测的规则"漂移。所有规则只看 index 与 index-1 两根 bar，可在回测里逐根调用。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

__all__ = [
    "BUY_SCORE",
    "FAMILY_WEIGHTS",
    "FUSED_STRATEGIES",
    "FUSION_BUY_SCORE",
    "FUSION_SELL_SCORE",
    "MIN_CONFIRM_RULES",
    "REVERSION_RULES",
    "SELL_SCORE",
    "TREND_RULES",
    "FamilyScore",
    "Signal",
    "evaluate_signals",
    "family_score",
    "fused_action",
    "fusion_score",
    "latest_signals",
    "score_families",
    "signal_score",
]

Direction = Literal["buy", "sell", "neutral"]

# —— 规则阈值（与 timing.py 的口径保持一致）——
RSI_OVERBOUGHT = 78.0
RSI_OVERSOLD = 30.0
KDJ_OVERSOLD = 20.0
KDJ_OVERBOUGHT = 80.0
KDJ_J_OVERBOUGHT = 100.0
ATR_STOP_MULTIPLE = 2.0

# 各信号在 signal_score 中的权重（0 表示只作展示、不参与打分）
_SIGNAL_WEIGHTS: dict[str, float] = {
    "ma_cross": 0.25,
    "ma_trend": 0.10,
    "macd_cross": 0.25,
    "macd_hist_flip": 0.10,
    "kdj_cross": 0.15,
    "boll_breakout": 0.15,
    "boll_reversion": 0.10,
    "donchian": 0.20,
    "rsi": 0.10,
    "atr_stop": 0.0,
}

# —— 策略分族 ——
# 分族依据是**规则实际度量什么**，不是指标族名：`boll_breakout` 是"上穿上轨"的突破（趋势），
# 而 `boll_reversion` 在"跌破下轨/远高于中轨"时触发（回归）；`kdj_cross` 只在超卖/超买区内
# 才算交叉（回归）；`rsi` 是纯区间规则（回归）。`atr_stop` 权重 0 且恒为 neutral，两族都不进
# —— 它的 `level` 由 decision.py 直接取用。
#
# TREND_RULES + REVERSION_RULES 必须恰等于 set(_SIGNAL_WEIGHTS) - {"atr_stop"}：以后新增
# 规则若忘了分族，test_quant_signals 会失败，而不是让它静默地永远不参与打分。
TREND_RULES: tuple[str, ...] = (
    "ma_cross",
    "ma_trend",
    "macd_cross",
    "macd_hist_flip",
    "boll_breakout",
    "donchian",
)
REVERSION_RULES: tuple[str, ...] = ("kdj_cross", "boll_reversion", "rsi")
FAMILY_WEIGHTS: dict[str, float] = {"trend": 0.6, "reversion": 0.4}
FUSED_STRATEGIES: tuple[str, ...] = ("trend_follow", "mean_reversion", "fusion")

# 规则集合 → 族名。family_score 靠它给结果填 name，这样手写 `family_score(signals, TREND_RULES)`
# 也能直接被 fusion_score 正确加权（name 空串会被当成未知族而静默忽略）。
_FAMILY_BY_RULES: dict[tuple[str, ...], str] = {
    TREND_RULES: "trend",
    REVERSION_RULES: "reversion",
}

# —— 阈值：先验选定，永不调参 ——
# `(65-50)/50 = 30%` 的权重须同向才够线。趋势族权重和 1.05，单条最大贡献是 ma_cross 的
# `0.25/1.05 = 23.8% → 61.9`：**65 是单指标永远够不到的最小整数线**。回归族只有 3 条规则，
# `kdj_cross` 一条就达 `0.15/0.35 → 71.4`，所以族结论额外要求 MIN_CONFIRM_RULES 条不同规则
# 同向，且该门槛作为独立一行展示在入场条件里（用户能看到"71 分却仍观望"是哪一条没过）。
# FUSION_BUY_SCORE=62 由趋势路径反推：回归族中性(50)时需 T≥70，即融合买入要么趋势确认充分、
# 要么两族共振。卖出线是买入线的镜像（62/42），规则符号对称，构造上不可能偏多。
#
# 为什么不在个股/窗口上搜索更优阈值：2 年日线的融合策略只产生 ~10 笔成交，期望值的标准误
# ≈ σ/√10，与均值同量级，搜出来的"最优阈值"是在拟合噪声。而那个数字会被当作"实测历史表现"
# 展示，实际上是多次尝试的最大值（选择性偏差）。**本模块不得存在遍历候选阈值的循环。**
BUY_SCORE = 65.0
SELL_SCORE = 45.0
FUSION_BUY_SCORE = 62.0
FUSION_SELL_SCORE = 42.0
MIN_CONFIRM_RULES = 2


@dataclass(frozen=True)
class Signal:
    name: str  # 稳定 id，回测按它匹配策略
    label: str  # 展示文案
    direction: Direction
    detail: str  # 面向用户的说明（含数值）
    strength: float  # 该信号的建议权重 0..1
    bar_index: int
    price: float
    level: float | None = None  # 仅 atr_stop：止损参考价


@dataclass(frozen=True)
class FamilyScore:
    """一个策略族（趋势/回归）在某一根 bar 上的合成分。

    `score` 用**固定分母**（该族全部规则的权重和），所以证据薄弱时会向 50 漂移——这与
    `signal_score` 的"在场权重分母"是两种语义，详见 `_weighted_score` 的说明。
    """

    name: str  # "trend" | "reversion"
    score: float  # 0..100，50 为中性
    buy_weight: float  # 同向买入的规则权重和（毛值）
    sell_weight: float  # 同向卖出的规则权重和（毛值）
    buy_rules: tuple[str, ...]  # 净贡献为 buy 的规则名（去重、按出现顺序）
    sell_rules: tuple[str, ...]
    direction: str  # "buy" | "sell" | "hold"

    @property
    def rules(self) -> tuple[str, ...]:
        """得出当前结论的规则；hold 时为空（没有规则在确认）。"""
        if self.direction == "buy":
            return self.buy_rules
        if self.direction == "sell":
            return self.sell_rules
        return ()


def _at(series: dict[str, list[float | None]], key: str, index: int) -> float | None:
    values = series.get(key)
    if values is None or index < 0 or index >= len(values):
        return None
    value = values[index]
    if value is None or not math.isfinite(value):
        return None
    return value


def _crossed(
    series: dict[str, list[float | None]], key: str, other: str, index: int, *, up: bool
) -> bool:
    """判断 index 处 key 是否上穿/下穿 other（需 index-1 也在同侧之外）。"""
    if index < 1:
        return False
    prev_key, prev_other = _at(series, key, index - 1), _at(series, other, index - 1)
    curr_key, curr_other = _at(series, key, index), _at(series, other, index)
    if None in (prev_key, prev_other, curr_key, curr_other):
        return False
    assert prev_key is not None and prev_other is not None
    assert curr_key is not None and curr_other is not None
    if up:
        return prev_key <= prev_other and curr_key > curr_other
    return prev_key >= prev_other and curr_key < curr_other


def _signal(
    name: str,
    label: str,
    direction: Direction,
    detail: str,
    index: int,
    price: float,
    *,
    weight: float | None = None,
    level: float | None = None,
) -> Signal:
    return Signal(
        name=name,
        label=label,
        direction=direction,
        detail=detail,
        strength=_SIGNAL_WEIGHTS.get(name, 0.0) if weight is None else weight,
        bar_index=index,
        price=price,
        level=level,
    )


def evaluate_signals(
    series: dict[str, list[float | None]], closes: Sequence[float], index: int
) -> list[Signal]:
    """在 index 处按规则表产出信号列表；任一规则缺数据则跳过该条。"""
    if index < 0 or index >= len(closes):
        return []
    price = float(closes[index])
    signals: list[Signal] = []

    def _add(
        name: str, label: str, direction: Direction, detail: str, level: float | None = None
    ) -> None:
        signals.append(_signal(name, label, direction, detail, index, price, level=level))

    # 1) 均线金叉/死叉 + 多头排列
    if _crossed(series, "ma5", "ma20", index, up=True):
        _add("ma_cross", "MA5 金叉 MA20", "buy", "短期均线上穿中期均线")
    elif _crossed(series, "ma5", "ma20", index, up=False):
        _add("ma_cross", "MA5 死叉 MA20", "sell", "短期均线下穿中期均线")
    ma5 = _at(series, "ma5", index)
    ma20 = _at(series, "ma20", index)
    ma60 = _at(series, "ma60", index)
    if ma5 is not None and ma20 is not None and ma60 is not None:
        if ma5 > ma20 > ma60 and price > ma20:
            _add("ma_trend", "多头排列", "buy", "MA5>MA20>MA60 且站上 MA20")

    # 2) MACD 金叉/死叉 + 柱状体翻正翻负
    if _crossed(series, "dif", "dea", index, up=True):
        _add("macd_cross", "MACD 金叉", "buy", "DIF 上穿 DEA")
    elif _crossed(series, "dif", "dea", index, up=False):
        _add("macd_cross", "MACD 死叉", "sell", "DIF 下穿 DEA")
    hist, prev_hist = _at(series, "macd_hist", index), _at(series, "macd_hist", index - 1)
    if hist is not None and prev_hist is not None:
        if prev_hist <= 0 < hist:
            _add("macd_hist_flip", "MACD 柱翻红", "buy", "红柱放大，动能转强")
        elif prev_hist >= 0 > hist:
            _add("macd_hist_flip", "MACD 柱翻绿", "sell", "绿柱出现，动能转弱")

    # 3) KDJ 金叉/死叉（只在超卖/超买区间的交叉才算数，避免噪声）
    k_prev = _at(series, "kdj_k", index - 1)
    d_prev = _at(series, "kdj_d", index - 1)
    if k_prev is not None and d_prev is not None:
        if _crossed(series, "kdj_k", "kdj_d", index, up=True) and min(k_prev, d_prev) < 30:
            _add("kdj_cross", "KDJ 低位金叉", "buy", "超卖区 K 上穿 D")
        elif _crossed(series, "kdj_k", "kdj_d", index, up=False) and max(k_prev, d_prev) > 70:
            _add("kdj_cross", "KDJ 高位死叉", "sell", "超买区 K 下穿 D")
    k_val = _at(series, "kdj_k", index)
    j_val = _at(series, "kdj_j", index)
    if k_val is not None and j_val is not None:
        if j_val > KDJ_J_OVERBOUGHT or k_val > KDJ_OVERBOUGHT:
            _add("kdj_cross", "KDJ 超买", "sell", f"K={k_val:.0f} J={j_val:.0f} 偏高")

    # 4) 布林带突破 / 跌破下轨 / 偏离中轨
    upper = _at(series, "boll_up", index)
    lower = _at(series, "boll_low", index)
    mid = _at(series, "boll_mid", index)
    prev_upper = _at(series, "boll_up", index - 1)
    prev_close = float(closes[index - 1]) if index >= 1 else None
    if upper is not None and prev_upper is not None and prev_close is not None:
        if price > upper and prev_close <= prev_upper:
            _add("boll_breakout", "突破布林上轨", "buy", "收盘站上上轨，趋势加速")
    if lower is not None and price < lower:
        _add("boll_reversion", "跌破布林下轨", "buy", "超跌区间，留意反弹")
    elif upper is not None and mid is not None and price > upper:
        _add("boll_reversion", "偏离布林中轨", "sell", "价格远高于中轨，回归压力")

    # 5) 唐奇安通道（海龟口径：窗口不含当前 bar）
    donchian_up = _at(series, "donchian_up", index)
    donchian_low = _at(series, "donchian_low", index)
    if donchian_up is not None and price > donchian_up:
        _add("donchian", "20 日新高", "buy", "突破唐奇安上轨")
    elif donchian_low is not None and price < donchian_low:
        _add("donchian", "20 日新低", "sell", "跌破唐奇安下轨")

    # 6) RSI 超买/超卖（阈值与 timing.py 一致）
    rsi_value = _at(series, "rsi", index)
    if rsi_value is not None:
        if rsi_value > RSI_OVERBOUGHT:
            _add("rsi", "RSI 过热", "sell", f"RSI≈{rsi_value:.0f}")
        elif rsi_value < RSI_OVERSOLD:
            _add("rsi", "RSI 超卖", "buy", f"RSI≈{rsi_value:.0f}")

    # 7) ATR 风控位（neutral，权重 0，仅提供参考价位）
    atr_value = _at(series, "atr14", index)
    if atr_value is not None and atr_value > 0:
        level = price - ATR_STOP_MULTIPLE * atr_value
        _add(
            "atr_stop",
            "ATR 止损位",
            "neutral",
            f"2×ATR({atr_value:.2f}) 止损参考 {level:.2f}",
            level=level,
        )
    return signals


def latest_signals(
    series: dict[str, list[float | None]], closes: Sequence[float]
) -> list[Signal]:
    """最新一根 bar 上的信号。"""
    return evaluate_signals(series, closes, len(closes) - 1)


def _contributes(signal: Signal, names: Sequence[str] | None) -> bool:
    """该信号是否计入打分。打分与记账两处共用，避免过滤条件各写一遍后漂移。"""
    if signal.strength <= 0 or signal.direction == "neutral":
        return False
    return names is None or signal.name in names


def _weighted_score(
    signals: Sequence[Signal],
    *,
    names: Sequence[str] | None = None,
    denominator: float | None = None,
) -> float | None:
    """把信号按权重折成 0-100 分（50 为中性）；分母为 0 时返回 None。

    分母有两种语义，绝不能混用：

    - `denominator=None`（`signal_score` 的历史行为）：除以**在场**信号的权重和。
      好处是"只有一条规则触发"时仍能得到满量程读数，坏处是单独一条 `rsi` 买入就读出
      100 分——**拿它当买入阈值会导致噪声触发**。
    - 传入固定分母（`family_score`）：证据不足时分数向 50 漂移，只有全族规则一致才到
      100。阈值只有在固定分母下才有意义。
    """
    total = 0.0
    weight_sum = 0.0
    for signal in signals:
        if not _contributes(signal, names):
            continue
        total += signal.strength * (1.0 if signal.direction == "buy" else -1.0)
        weight_sum += signal.strength
    scale = weight_sum if denominator is None else denominator
    if scale == 0:
        return None
    return max(0.0, min(100.0, 50 + 50 * total / scale))


def signal_score(signals: Sequence[Signal]) -> float | None:
    """把信号加权成 0-100 的择时评分（50 为中性）；无有效信号时返回 None。

    注意分母是**在场**信号的权重和——这是 /quant 页"信号强度"的既有口径，不要拿它做
    阈值判断（见 `_weighted_score`）。阈值判断一律用 `family_score`。
    """
    return _weighted_score(signals)


def _family_denominator(names: Sequence[str]) -> float:
    return sum(_SIGNAL_WEIGHTS.get(name, 0.0) for name in names)


def _family_verdict(score: float, buy_rules: Sequence[str], sell_rules: Sequence[str]) -> str:
    """族结论：分数够线**且**至少 MIN_CONFIRM_RULES 条不同规则同向。"""
    if score >= BUY_SCORE and len(buy_rules) >= MIN_CONFIRM_RULES:
        return "buy"
    if score <= SELL_SCORE and len(sell_rules) >= MIN_CONFIRM_RULES:
        return "sell"
    return "hold"


def family_score(signals: Sequence[Signal], names: Sequence[str]) -> FamilyScore:
    """某族在给定信号上的合成分（固定分母）。无任何信号时中性 50，**不是 None**。"""
    score = _weighted_score(signals, names=names, denominator=_family_denominator(names))
    assert score is not None  # 族权重和恒 > 0，只可能是 None 的分母为 0 分支
    buy_weight = 0.0
    sell_weight = 0.0
    net: dict[str, float] = {}
    for signal in signals:
        if not _contributes(signal, names):
            continue
        if signal.direction == "buy":
            buy_weight += signal.strength
            net[signal.name] = net.get(signal.name, 0.0) + signal.strength
        else:
            sell_weight += signal.strength
            net[signal.name] = net.get(signal.name, 0.0) - signal.strength
    # 同一条规则可以在一根 bar 上同时给出买和卖（如 kdj_cross 低位金叉 + 超买），
    # 此时净贡献抵消——它既不算买方的确认，也不算卖方。用净符号而不是"出现过"。
    buy_rules = tuple(name for name, value in net.items() if value > 0)
    sell_rules = tuple(name for name, value in net.items() if value < 0)
    return FamilyScore(
        name=_FAMILY_BY_RULES.get(tuple(names), ""),
        score=score,
        buy_weight=buy_weight,
        sell_weight=sell_weight,
        buy_rules=buy_rules,
        sell_rules=sell_rules,
        direction=_family_verdict(score, buy_rules, sell_rules),
    )


def fusion_score(scores: Sequence[FamilyScore]) -> float:
    """把各族分数按 FAMILY_WEIGHTS 融合成 0-100；未知族名忽略，全无则中性 50。"""
    total = 0.0
    weight = 0.0
    for item in scores:
        family = FAMILY_WEIGHTS.get(item.name)
        if family is None:
            continue
        total += item.score * family
        weight += family
    if weight == 0:
        return 50.0
    return max(0.0, min(100.0, total / weight))


def score_families(signals: Sequence[Signal]) -> dict[str, FamilyScore]:
    """趋势/回归两族的分数——`fused_action` 与 UI 都用这一份。"""
    return {
        "trend": family_score(signals, TREND_RULES),
        "reversion": family_score(signals, REVERSION_RULES),
    }


def fused_action(strategy: str, signals: Sequence[Signal]) -> str:
    """"trend_follow"/"mean_reversion"/"fusion" 三种策略的动作，回测与计划共用。

    用**水平**判定而非交叉判定：引擎只在空仓时进、持仓时出，水平规则对首次触发等价于
    交叉规则，且不需要路径状态——实时计划也就只需要最后一根 bar。
    """
    scores = score_families(signals)
    trend, reversion = scores["trend"], scores["reversion"]
    if strategy == "trend_follow":
        return trend.direction
    if strategy == "mean_reversion":
        return reversion.direction
    if strategy != "fusion":
        return "hold"
    fused = fusion_score([trend, reversion])
    # 两族冲突（一族买一族卖）时融合分自然落在 50 附近，两条分支都不会命中 → hold。
    if fused >= FUSION_BUY_SCORE and "buy" in (trend.direction, reversion.direction):
        return "buy"
    if fused <= FUSION_SELL_SCORE and "sell" in (trend.direction, reversion.direction):
        return "sell"
    return "hold"
