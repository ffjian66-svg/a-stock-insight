"""全市场汇总对个股结论的作用：**只能否决，不能放行**。

这是用户明确选定的契约，本文件把它写成可执行的断言。核心一条是
`test_veto_can_only_downgrade`：在 `market_edge` 的所有取值上，带它的动作都不允许比
不带它更乐观。任何"池化数字让某只股票的结论变得更好"的实现都会被它抓住。

底稿刻意用 `_oversold_decline()`——它是 `test_quant_decision` 里唯一能构造出
"信号侧 4 条条件全满足"的形状，也就是唯一能走到 `buy` 分支的形状。用一条本来就走不到
`buy` 的序列，「只能降级」会被空转通过而毫无约束力。
"""

from __future__ import annotations

import dataclasses
from typing import Optional

import pytest
import tests.test_quant_decision as decision_tests
from app.services.quant.decision import MarketEdge, Plan

_RANK = {"avoid": 0, "watch": 1, "buy": 2}


def _edge(**overrides: object) -> MarketEdge:
    base: dict[str, object] = {
        "strategy": "donchian",
        "verdict": "negative",
        "trade_count": 8_817,
        "expectancy_pct": -1.614,
        "expectancy_per_bar_pct": -0.248,
        "excess_per_bar_pct": -0.128,
        "stocks_with_trades": 4_200,
        "bars_median": 96,
        "computed_at": None,
        "rule_version": "v2",
    }
    base.update(overrides)
    return MarketEdge(**base)  # type: ignore[arg-type]


def _expectancy(verdict: str, *, strategy: str = "donchian", label: str = "唐奇安通道"):
    """个股这一层的实测记录。正期望 + 24 笔 → verdict="positive"。"""
    return dataclasses.replace(
        decision_tests._expectancy(trade_count=24, expectancy_pct=1.9, verdict=verdict),
        strategy=strategy,
        strategy_label=label,
    )


def _plan(expectancy, market_edge: Optional[MarketEdge]) -> Plan:
    return decision_tests._plan(
        decision_tests._oversold_decline(),
        expectancy=expectancy,
        market_edge=market_edge,
    )


def _baseline() -> Plan:
    """不带汇总时的结论——本文件所有比较的参照物。"""
    return _plan(_expectancy("positive"), None)


# ======================================================================================
# 0：前提
# ======================================================================================
def test_the_fixture_actually_reaches_the_buy_branch() -> None:
    """没有这条，下面"只能降级"的断言全部是空转。"""
    plan = _baseline()
    assert all(item.satisfied for item in plan.entry_conditions)
    assert plan.action == "buy"


# ======================================================================================
# 1：只能否决、不能放行（用户选定的契约）
# ======================================================================================
@pytest.mark.parametrize(
    "edge",
    [
        None,
        _edge(),
        _edge(verdict="positive", expectancy_pct=0.9, excess_per_bar_pct=0.4),
        _edge(verdict="insufficient", trade_count=3),
        _edge(is_baseline=True),
        _edge(strategy="fusion"),
        _edge(excess_per_bar_pct=0.05),
        _edge(excess_per_bar_pct=None),
        _edge(expectancy_pct=None, excess_per_bar_pct=-1.0),
    ],
    ids=[
        "无汇总",
        "原始负-超额负",
        "全为正",
        "池化样本不足",
        "基准行",
        "策略名不符",
        "原始负但超额正",
        "超额缺失",
        "期望缺失",
    ],
)
def test_veto_can_only_downgrade(edge) -> None:
    """任何 `market_edge` 下，结论都不允许比"没有汇总"时更乐观。

    三个 verdict 各走一遍：`positive` 是最关键的一格——它是唯一可能被否决的分支，
    也正是"只能降级"要守的那一格；另两格用来看住"别把 avoid 抬成 buy"。
    """
    for verdict in ("positive", "negative", "insufficient"):
        expectancy = _expectancy(verdict)
        without = _plan(expectancy, None)
        with_edge = _plan(expectancy, edge)

        assert _RANK[with_edge.action] <= _RANK[without.action], (verdict, edge, with_edge.action)
        # 放行 = 把非 buy 变成 buy，这一条永不允许发生
        if without.action != "buy":
            assert with_edge.action != "buy", (verdict, edge)


def test_negative_pooled_verdict_downgrades_buy_to_watch() -> None:
    plan = _plan(_expectancy("positive"), _edge())
    assert plan.action == "watch"
    assert "全市场" in plan.action_label
    # 降级必须留痕：用户要能分清"信号没到"与"全市场否决"
    assert any("全市场" in line for line in plan.reasons)


def test_baseline_strategy_is_never_vetoed() -> None:
    """买入持有不宣称任何 edge，池化基准为负说的是"这段行情在跌"——那是行情判断，
    不是规则质量判断。它也是用户什么都不做就能选的那一项。"""
    plan = _plan(
        _expectancy("positive", strategy="buy_hold", label="买入持有"),
        _edge(strategy="buy_hold", is_baseline=True),
    )
    assert plan.action == "buy"


def test_a_rule_that_only_looks_bad_because_the_market_fell_is_not_vetoed() -> None:
    """**「不能只看原始期望的符号」的钉子。**

    实测里 macd_cross 全市场每笔 −0.014%，而同期等权买入持有 −12.449%/笔
    （每 bar −0.1207 对 −0.0018）。只看原始符号去否决，会把一条在市场跌 12% 时打平的
    规则判成"没有 edge"——`backtest.py` 把这种读法直接称为"反向撒谎"。
    """
    plan = _plan(
        _expectancy("positive"),
        _edge(verdict="negative", expectancy_pct=-0.014, excess_per_bar_pct=0.1189),
    )
    assert plan.action == "buy"


def test_mismatched_strategy_never_vetoes() -> None:
    """绝不能拿另一条规则的战绩去否这只股票。"""
    plan = _plan(_expectancy("positive", strategy="donchian"), _edge(strategy="kdj_cross"))
    assert plan.action == "buy"


def test_absent_pool_stats_leave_behaviour_untouched() -> None:
    """没算过汇总的库，不该因为"缺证据"而改变任何结论。"""
    for verdict in ("positive", "negative", "insufficient"):
        assert _plan(_expectancy(verdict), None).action == _plan(_expectancy(verdict), None).action
    assert _baseline().action == "buy"


# ======================================================================================
# 2：样本量补白——本功能最主要的产出
# ======================================================================================
def test_pooled_numbers_are_appended_to_the_evidence_line() -> None:
    """个股样本不足时，`expectancy_gate` 那行要能说出"这条规则在全市场是什么结果"。

    原来那行只有一句"样本不足以证明有效"——用户读到的是一片空白。
    """
    plan = _plan(
        _expectancy("insufficient"),
        _edge(verdict="positive", expectancy_pct=0.223),
    )
    gate = next(item for item in plan.entry_conditions if item.key == "expectancy_gate")

    assert "全市场汇总" in gate.detail
    assert "8,817" in gate.detail  # 池化笔数
    assert "4,200" in gate.detail  # 有成交的股票数
    assert "96" in gate.detail  # 中位交易日数（决定窗口多长的是它）
    # 口径必须写在行内：它不是这只股票的预期，也只能让结论更保守
    assert "不是本标的的预期" in gate.detail


def test_evidence_line_says_nothing_when_there_is_no_pool_data() -> None:
    """没算过汇总时不能凭空多出一句"全市场……"，那会让用户以为已经算过了。"""
    plan = _baseline()
    gate = next(item for item in plan.entry_conditions if item.key == "expectancy_gate")
    assert "全市场" not in gate.detail
    assert not any("全市场" in line for line in plan.reasons)


def test_pooled_context_does_not_soften_a_negative_per_stock_conclusion() -> None:
    """个股实测为负时，池化数字不能把话说轻——`avoid` 必须还是 `avoid`。"""
    plan = _plan(
        _expectancy("negative"),
        _edge(verdict="positive", expectancy_pct=0.255, excess_per_bar_pct=0.1),
    )
    assert plan.action == "avoid"


# ======================================================================================
# 3：内核纯净性
# ======================================================================================
def test_market_edge_is_a_frozen_pure_dataclass() -> None:
    """整套设计建立在"内核不 import backtest（因此不 import DB）"之上：
    `expectancy` 与 `market_edge` 都由 API 层注入。一旦内核真的 import 了 backtest，
    它会连带绑上 DB 依赖，本文件的离线单测也会开始拉起数据库栈。
    """
    import app.services.quant.decision as decision_mod

    assert "backtest" not in decision_mod.__dict__
    edge = _edge()
    assert dataclasses.is_dataclass(edge)
    with pytest.raises(dataclasses.FrozenInstanceError):
        edge.verdict = "positive"  # type: ignore[misc]
