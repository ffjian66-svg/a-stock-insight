"""综合评分：把 [[quant.factors]] 的横截面因子打分翻译成对外契约。

对外的 4 个解释分组名、组间权重与 coverage 语义与旧版完全一致，变化的是**分数口径**：
旧版是写死阈值的绝对分，新版是横截面分位分（详见 quant/factors.py 的模块说明）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Optional

from app.services.quant import factors

RULE_VERSION = "v2"

_REASONS: dict[str, str] = {
    "趋势与动量": "20/60 日动量、5 日反转与换手活跃度的横截面分位",
    "质量与估值": "盈利收益率、账面市值比、ROE 与利润增速的横截面分位",
    "新闻情绪": "近三日相关新闻的结构化情绪结果",
    "波动风险": "20 日波动率的横截面分位，波动越低得分越高",
}


@dataclass(frozen=True)
class ScoreResult:
    total: Optional[float]
    technical: Optional[float]
    fundamental: Optional[float]
    sentiment: Optional[float]
    risk: Optional[float]
    coverage: float
    risk_level: str
    explanations: list[dict[str, object]]
    # 分组 -> 子因子明细（原始值/分位/权重），落 FactorSnapshot 时复用；单股降级路径可为空
    detail: Optional[dict[str, dict[str, object]]] = None


def _explanations(score: factors.PanelScore) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for group in factors.GROUPS:
        value = score.groups.get(group)
        if value is None:
            continue
        parts = [
            f"{item['label']} {round(float(item['raw']), 2)}"  # type: ignore[arg-type]
            for item in factors.group_factors(score.detail, group)
            if item.get("raw") is not None
        ]
        entries.append(
            {
                "factor": group,
                "value": " / ".join(parts),
                "score": value,
                "reason": _REASONS[group],
            }
        )
    return entries


def _to_result(score: factors.PanelScore) -> ScoreResult:
    risk = score.groups.get("波动风险")
    risk_level = (
        "低"
        if risk is not None and risk >= 72
        else "中"
        if risk is not None and risk >= 48
        else "高"
    )
    return ScoreResult(
        total=score.total,
        technical=score.groups.get("趋势与动量"),
        fundamental=score.groups.get("质量与估值"),
        sentiment=score.groups.get("新闻情绪"),
        risk=risk,
        coverage=score.coverage,
        risk_level=risk_level,
        explanations=_explanations(score),
        detail=score.detail,
    )


def score_panel(
    closes: Mapping[str, Sequence[float]],
    fundamentals: Mapping[str, Mapping[str, Optional[float]]] | None = None,
    sentiments: Mapping[str, Optional[float]] | None = None,
) -> dict[str, ScoreResult]:
    """全市场横截面打分：{ts_code: 日线收盘序列} → {ts_code: ScoreResult}。

    必须整块传入：分位分只有在横截面里才有意义，单只股票无法自证高低。
    """
    fundamentals = fundamentals or {}
    sentiments = sentiments or {}
    panel = {
        code: factors.raw_factors(values, fundamentals.get(code), sentiments.get(code))
        for code, values in closes.items()
    }
    return {code: _to_result(score) for code, score in factors.panel_scores(panel).items()}


def score_stock(
    closes: Sequence[float],
    fundamentals: Mapping[str, Optional[float]] | None = None,
    sentiment: Optional[float] = None,
) -> ScoreResult:
    """单只股票的便捷入口（面板只有它自己）。

    注意：单只股票的分位分恒为中性 50（没有比较对象），这里仅用于冒烟与降级路径，
    真实评分请走 `score_panel`。
    """
    code = "__single__"
    return score_panel({code: closes}, {code: fundamentals or {}}, {code: sentiment})[code]
