"""多因子横截面打分：因子提取 → 分位标准化 → 分组加权合成。

相对旧版 `scoring.py`（写死阈值的绝对分）的核心变化：因子先在**全市场横截面上**
转成 0-100 分位分，再按组加权。好处是"高分"天然意味着"比同行好"，不会被整体行情
（牛市里人人波动都大、熊市里人人 PE 都低）带偏；代价是分数变成相对值，同一只股票
会随同业变化而变，且**全站综合评分口径随之改变**（已由 rule_version=v2 标记）。

标准化用分位排名而非 z 值：z 值需要足够大的样本与近似正态假设，分位排名对任意
n≥2 都有定义、且天然免疫极端值（无需额外缩尾）。

唯一的例外是新闻情绪——它本身已是 -1..1 的绝对情绪值，且带新闻的股票往往只有
几十只，横截面分位没有统计意义，故保留 `(s+1)×50` 的绝对映射（分组权重与旧版
完全一致，coverage 语义因此不变）。
"""

from __future__ import annotations

import bisect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Optional

from app.services.quant.indicators import volatility

__all__ = [
    "FACTOR_DEFS",
    "GROUP_WEIGHTS",
    "GROUPS",
    "MIN_TOTAL_COVERAGE",
    "FactorDef",
    "PanelScore",
    "factor_definitions",
    "panel_scores",
    "raw_factors",
]

MIN_TOTAL_COVERAGE = 0.6  # 覆盖度低于该值不给综合分（与旧版一致）


@dataclass(frozen=True)
class FactorDef:
    name: str
    label: str
    group: str
    weight: float  # 组内权重
    direction: int  # +1 数值越大越好；-1 数值越小越好


GROUPS: tuple[str, ...] = ("趋势与动量", "质量与估值", "新闻情绪", "波动风险")
GROUP_WEIGHTS: dict[str, float] = {
    "趋势与动量": 0.40,
    "质量与估值": 0.30,
    "新闻情绪": 0.20,
    "波动风险": 0.10,
}

FACTOR_DEFS: tuple[FactorDef, ...] = (
    FactorDef("momentum_20", "20日动量", "趋势与动量", 0.35, 1),
    FactorDef("momentum_60", "60日动量", "趋势与动量", 0.30, 1),
    FactorDef("reversal_5", "5日反转", "趋势与动量", 0.20, -1),
    FactorDef("turnover", "换手活跃度", "趋势与动量", 0.15, 1),
    FactorDef("value_ep", "盈利收益率", "质量与估值", 0.35, 1),
    FactorDef("value_bp", "账面市值比", "质量与估值", 0.15, 1),
    FactorDef("quality_roe", "ROE", "质量与估值", 0.30, 1),
    FactorDef("quality_growth", "利润增速", "质量与估值", 0.20, 1),
    FactorDef("sentiment", "新闻情绪", "新闻情绪", 1.00, 1),
    FactorDef("low_vol", "低波动", "波动风险", 1.00, 1),
)

_DEFS_BY_NAME = {definition.name: definition for definition in FACTOR_DEFS}


@dataclass(frozen=True)
class PanelScore:
    """单只股票的横截面打分结果。"""

    groups: dict[str, Optional[float]]  # 4 个分组得分（0-100 或 None）
    coverage: float
    total: Optional[float]
    detail: dict[str, dict[str, object]]  # 分组 -> 子因子明细（含分位与权重）


def factor_definitions() -> list[dict[str, object]]:
    """因子定义表，供 API 展示（前端据此说明评分口径）。"""
    return [
        {
            "name": definition.name,
            "label": definition.label,
            "group": definition.group,
            "weight": definition.weight,
            "direction": definition.direction,
        }
        for definition in FACTOR_DEFS
    ]


def group_factors(
    detail: Mapping[str, Mapping[str, object]], group: str
) -> list[dict[str, object]]:
    """取某个分组下的子因子明细列表。

    `detail` 的值是异构 dict，mypy 只能看到 object；这里统一收口，避免每个调用方
    都写一遍 isinstance 收窄。
    """
    entries = detail.get(group, {}).get("factors")
    return [item for item in entries if isinstance(item, dict)] if isinstance(entries, list) else []


def _last(values: Sequence[object]) -> Optional[float]:
    for value in reversed(values):
        if value is not None:
            return float(value)  # type: ignore[arg-type]
    return None


def raw_factors(
    closes: Sequence[float],
    fundamentals: Mapping[str, Optional[float]] | None = None,
    sentiment: Optional[float] = None,
) -> dict[str, Optional[float]]:
    """抽取单只股票的原始因子值；算不出来的因子给 None（不参与横截面、也不拉低得分）。"""
    fundamentals = fundamentals or {}
    size = len(closes)
    values: dict[str, Optional[float]] = dict.fromkeys(_DEFS_BY_NAME)

    def _momentum(period: int) -> Optional[float]:
        if size <= period or not closes[-period - 1]:
            return None
        return (closes[-1] / closes[-period - 1] - 1) * 100

    values["momentum_20"] = _momentum(20)
    values["momentum_60"] = _momentum(60)
    values["reversal_5"] = _momentum(5)

    turnover = fundamentals.get("turnover_rate")
    values["turnover"] = None if turnover is None else float(turnover)

    pe_ttm = fundamentals.get("pe_ttm")
    values["value_ep"] = 1 / pe_ttm if pe_ttm and pe_ttm > 0 else None
    pb = fundamentals.get("pb")
    values["value_bp"] = 1 / pb if pb and pb > 0 else None

    roe = fundamentals.get("roe")
    values["quality_roe"] = None if roe is None else float(roe)
    growth = fundamentals.get("profit_growth")
    values["quality_growth"] = None if growth is None else float(growth)

    values["sentiment"] = None if sentiment is None else (sentiment + 1) * 50

    vol = _last(volatility(closes)) if size >= 2 else None
    values["low_vol"] = None if vol is None else -vol * 100
    return values


def _percentile(value: float, sorted_values: Sequence[float]) -> Optional[float]:
    """分位分 0-100：最小值 0、最大值 100、并列取平均名次。"""
    size = len(sorted_values)
    if size == 0:
        return None
    if size == 1:
        return 50.0
    low = bisect.bisect_left(sorted_values, value)
    high = bisect.bisect_right(sorted_values, value)
    rank = (low + high - 1) / 2
    return rank / (size - 1) * 100


def panel_scores(panel: Mapping[str, Mapping[str, Optional[float]]]) -> dict[str, PanelScore]:
    """横截面打分：panel 为 {ts_code: {factor_name: 原始值}}。"""
    # 参考分布按「方向归一后」的值建立：direction=-1 的因子（如 5 日反转）取负后再排名，
    # 这样所有因子的分位都是「越大越好」。
    reference: dict[str, list[float]] = {}
    for values in panel.values():
        for name, raw in values.items():
            definition = _DEFS_BY_NAME.get(name)
            if raw is None or definition is None:
                continue
            reference.setdefault(name, []).append(float(raw) * definition.direction)
    for bucket in reference.values():
        bucket.sort()

    results: dict[str, PanelScore] = {}
    for code, values in panel.items():
        groups: dict[str, Optional[float]] = {}
        detail: dict[str, dict[str, object]] = {}
        for group in GROUPS:
            definitions = [item for item in FACTOR_DEFS if item.group == group]
            weighted = 0.0
            weight_sum = 0.0
            entries: list[dict[str, object]] = []
            for definition in definitions:
                raw = values.get(definition.name)
                if raw is None:
                    entries.append(
                        {
                            "factor": definition.name,
                            "label": definition.label,
                            "raw": None,
                            "score": None,
                            "percentile": None,
                            "weight": definition.weight,
                            "direction": definition.direction,
                        }
                    )
                    continue
                score: Optional[float]
                if definition.name == "sentiment":
                    score = float(raw)  # 绝对情绪映射，不做横截面分位
                else:
                    score = _percentile(
                        float(raw) * definition.direction, reference.get(definition.name, [])
                    )
                entries.append(
                    {
                        "factor": definition.name,
                        "label": definition.label,
                        "raw": float(raw),
                        "score": score,
                        "percentile": score,
                        "weight": definition.weight,
                        "direction": definition.direction,
                    }
                )
                if score is None:
                    continue
                weighted += score * definition.weight
                weight_sum += definition.weight
            groups[group] = round(weighted / weight_sum, 1) if weight_sum else None
            detail[group] = {"factors": entries, "weight": GROUP_WEIGHTS[group]}

        present = [
            (score, GROUP_WEIGHTS[group])
            for group, score in groups.items()
            if score is not None
        ]
        coverage = round(sum(weight for _, weight in present), 2)
        total = (
            round(sum(score * weight for score, weight in present) / coverage, 1)
            if coverage >= MIN_TOTAL_COVERAGE
            else None
        )
        results[code] = PanelScore(groups=groups, coverage=coverage, total=total, detail=detail)
    return results
