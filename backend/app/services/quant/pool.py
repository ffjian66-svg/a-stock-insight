"""跨股票池化：把 11 条规则在**全市场**上的实测结果折成一张汇总表。

## 为什么需要它

2 年日线是**按需回补**的（只有被真正打开过的股票才有深历史），库内 5,564 只里有 5,398 只
只有 60–119 根。于是任何单只股票都凑不到 `INSUFFICIENT_TRADES`(20) 笔——`/expectancy/{code}`
上 11 行有 6 行是"样本不足"。跨股票池化是唯一能把样本量抬上一个量级的办法：
实测 5,515 只 / 574,286 根 / 203,874 笔。

## 池化引入的偏差（每条都由 API 层如实披露，见 `backtest.POOL_CAVEAT`）

- **生存偏差**：库里只有当前上市的股票，退市股缺席，结果系统性偏乐观。
- **成交不独立**：所有股票共享同一段行情（中位 96 个交易日 ≈ 4.5 个月），
  20 万笔远不是 20 万个独立观测，有效样本量小得多——所以这里**不给任何显著性结论**，
  也不做 t 检验（按 n=20 万算出来的显著性会严重高估）。
- **按笔等权**：历史更长的股票贡献更多笔。
- 每只股票的末笔都是样本末尾强制平仓（`forced_end_share`），不是规则出场。

## 单独看池化均值会撒谎

实测（5,515 只 / 574,286 根 / 中位 96 个交易日）：`mean_reversion` 池化每笔 **+0.255%**，
而**股票中位数 −1.11%**、正股占比 32.3%——右偏分布，少数股票的盈利把均值抬到中位数之上。
**每一个策略的股票中位数都是负的**，包括那三个均值为正的。所以
`median_stock_expectancy_pct` / `positive_stock_share` 与均值同等重要，页面必须并排展示，
只显示均值就是在骗人。

这两个度量的**分母很要紧**，而且实测下来比想象中紧得多：`mean_reversion` 有成交的是 5,307 只，
而达到 `POSITIVE_STOCK_MIN_TRADES`(5) 笔的只有 **344 只**（各规则 130–4,112 只不等）。
两个分母算出来的数**差得很多**——不加门槛时 `mean_reversion` 是「中位 −0.64% / 正股 45.1%」，
加了门槛变成「中位 −1.11% / 正股 32.3%」。少笔数的那批股票（只有 1–4 笔）成绩明显更好，
原因很直白：单笔近似随机，而反复成交的那批才是这条规则的稳态表现。所以门槛不能省，
分母也必须每行印出来（`stock_denominator`），否则读者会把"344 只的中位数"读成"全部 5,307 只的"。

一个结构性的后果：**`buy_hold` 的分母永远是 0**——它每只股票恰好成交 1 笔，
永远达不到 5 笔门槛，于是它的股级中位数与正股占比恒为 `None`（页面渲染 `--`）。
这不是缺数据，是这两个度量对它不适用；它的样本量由 `stocks_with_trades` 单独给出。

同理必须有基准：这段窗口里等权买入持有 **−12.449%/笔**（市场在跌），于是 `macd_cross` 的
−0.014%/笔其实是**大幅跑赢**（按 bar 算 −0.0018 vs 市场 −0.1207）。只看原始期望的符号
去判断"有没有 edge"，正是 `backtest.py` 称为"反向撒谎"的读法。

## 本模块是纯计算

不 import DB、不 import provider、不写库——逐股的取数与落库在 `app.services.pool`。
这样聚合数学可以用手工构造的成交离线核对（见 `tests/test_quant_pool.py`），
也守住 `quant/__init__` 声明的"整个包不碰 DB / provider"。
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from app.services.quant import backtest as backtest_engine
from app.services.quant.backtest import BacktestParams, verdict_for
from app.services.quant.indicators import MA_WINDOWS
from app.services.quant.performance import parse_date

__all__ = [
    "POOL_MIN_BARS",
    "POSITIVE_STOCK_MIN_TRADES",
    "PoolAccumulator",
    "PoolRow",
]

# 计入池化的最少根数。取**最长均线窗口**而不是拍一个数字：`ma60` 要 60 根才产出第一个
# 有效值，不足 60 根的股票在 MA60 上是全 None，计进去等于往样本里掺零。
# 从 MA_WINDOWS 推导而不是写 60，是为了 MA_WINDOWS 变化时这个门槛跟着动。
POOL_MIN_BARS = max(MA_WINDOWS)

# 计算「股票中位数」与「正股占比」时，每只股票的最少成交笔数。
# 不设这个门槛，两个度量会被只有 1–2 笔的股票主导——恰好与它们"描述典型股票"的目的相反。
# 门槛与分母（`stock_denominator`）都会随结果一起披露，不能只给一个数。
POSITIVE_STOCK_MIN_TRADES = 5


@dataclass
class _Totals:
    """一条规则的累加器。刻意只存标量 + 每股一行，不保留 `Trade` 对象：
    20 万笔 frozen dataclass 在小内存机上是纯浪费，而聚合只需要这几个和数。"""

    trade_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    sum_win_pct: float = 0.0
    sum_loss_pct: float = 0.0
    gross_win: float = 0.0  # 按金额，供 profit_factor（与 _trade_stats 同口径）
    gross_loss: float = 0.0
    sum_hold_bars: int = 0
    forced_end: int = 0
    # 每股 (笔数, 该股期望%)，供中位数与正股占比。约 5.5k 个 tuple，可忽略。
    per_stock: list[tuple[int, float]] = field(default_factory=list)


@dataclass(frozen=True)
class PoolRow:
    """一条规则的全市场汇总。字段与 `db.models.StrategyPoolStats` 1:1。"""

    strategy: str
    trade_count: int
    # 这条规则在多少只股票上真出了成交。与 `stock_denominator` 不是一回事：那个是
    # 「成交笔数 ≥ POSITIVE_STOCK_MIN_TRADES」的子集，供中位数与正股占比用。
    stocks_with_trades: int
    win_rate: Optional[float]
    avg_win_pct: Optional[float]
    avg_loss_pct: Optional[float]
    profit_factor: Optional[float]
    expectancy_pct: Optional[float]
    expectancy_per_bar_pct: Optional[float]
    avg_hold_bars: Optional[float]
    forced_end_trades: int
    forced_end_share: Optional[float]
    benchmark_expectancy_pct: Optional[float]
    benchmark_expectancy_per_bar_pct: Optional[float]
    excess_per_bar_pct: Optional[float]
    median_stock_expectancy_pct: Optional[float]
    positive_stock_share: Optional[float]
    positive_stock_min_trades: int
    stock_denominator: int
    verdict: str


def _expectancy(totals: _Totals) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """折出 (期望/笔, 每 bar 期望, 平均持有 bar 数)。

    与 `backtest.trade_expectancy` **同一个式子**（`win_rate·avg_win + (1-win_rate)·avg_loss`，
    代数上等于 `mean(pnl_pct)`），这样池化与个股两条路径不可能给出同数字不同解释。
    赢/亏的划分也照抄那边：`pnl > 0` 为赢。
    """
    if not totals.trade_count:
        return None, None, None
    win_rate = totals.win_count / totals.trade_count
    avg_win = totals.sum_win_pct / totals.win_count if totals.win_count else 0.0
    avg_loss = totals.sum_loss_pct / totals.loss_count if totals.loss_count else 0.0
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss
    avg_hold = totals.sum_hold_bars / totals.trade_count
    per_bar = expectancy / avg_hold if avg_hold else None
    return expectancy, per_bar, avg_hold


class PoolAccumulator:
    """逐股喂入，最后 `finalize` 出 11 行。取数/落库不在这里。"""

    def __init__(self) -> None:
        self.bars_total = 0
        self._totals: dict[str, _Totals] = {
            name: _Totals() for name in backtest_engine.all_strategy_names()
        }
        # 每只**计入**的股票的首/末交易日，供 window_from/window_to 取中位数。
        # 取中位数而不是 min/max：把 2 年并集当"覆盖区间"、而 97% 的样本只贡献尾部
        # 4.5 个月，也是反向撒谎。
        self._first_days: list[date] = []
        self._last_days: list[date] = []
        self._bar_counts: list[int] = []

    @property
    def stocks_used(self) -> int:
        return len(self._bar_counts)

    def add_stock(self, rows: Sequence[dict[str, object]], params: BacktestParams) -> bool:
        """把一只股票并入汇总。根数不足 `POOL_MIN_BARS` 时整只跳过并返回 False。"""
        if len(rows) < POOL_MIN_BARS:
            return False
        first = parse_date(rows[0].get("trade_date") or rows[0].get("date"))
        last = parse_date(rows[-1].get("trade_date") or rows[-1].get("date"))
        self.bars_total += len(rows)
        self._bar_counts.append(len(rows))
        if first is not None and last is not None:
            self._first_days.append(first)
            self._last_days.append(last)

        for name, totals in self._totals.items():
            # 每条规则各跑一次；同一批 bars 下 buy_hold 就是这只股票的"买入持有"基准。
            result = backtest_engine.backtest(rows, name, params)
            stock_count = 0
            stock_pnl = 0.0
            for trade in result.trades:
                totals.trade_count += 1
                totals.sum_hold_bars += trade.hold_bars
                stock_count += 1
                stock_pnl += trade.pnl_pct
                if trade.pnl > 0:
                    totals.win_count += 1
                    totals.sum_win_pct += trade.pnl_pct
                    totals.gross_win += trade.pnl
                else:
                    totals.loss_count += 1
                    totals.sum_loss_pct += trade.pnl_pct
                    totals.gross_loss += abs(trade.pnl)
                if trade.exit_reason == "end":
                    totals.forced_end += 1
            if stock_count:
                totals.per_stock.append((stock_count, stock_pnl / stock_count))
        return True

    def finalize(self, *, universe_total: int, rule_version: str) -> list[PoolRow]:
        """折成 11 行。基准（买入持有）的行也在这里给出，供页面并排对照。"""
        raw = {name: _expectancy(totals) for name, totals in self._totals.items()}
        baseline_pct, baseline_per_bar, _ = raw.get(
            backtest_engine.BASELINE_STRATEGY, (None, None, None)
        )

        rows: list[PoolRow] = []
        for name, totals in self._totals.items():
            expectancy, per_bar, avg_hold = raw[name]
            # 基准行自己就是基准：超额记 0，而不是拿它减自己（那会得到 0 之外的噪声）。
            # 与 strategy_routes 的基准行处理一致。
            if name == backtest_engine.BASELINE_STRATEGY:
                excess = 0.0
            elif per_bar is not None and baseline_per_bar is not None:
                excess = per_bar - baseline_per_bar
            else:
                excess = None

            counts = [item for item in totals.per_stock if item[0] >= POSITIVE_STOCK_MIN_TRADES]
            median = statistics.median([item[1] for item in counts]) if counts else None
            share = (
                sum(1 for item in counts if item[1] > 0) / len(counts) if counts else None
            )
            rows.append(
                PoolRow(
                    strategy=name,
                    trade_count=totals.trade_count,
                    stocks_with_trades=len(totals.per_stock),
                    win_rate=(
                        totals.win_count / totals.trade_count if totals.trade_count else None
                    ),
                    avg_win_pct=(
                        totals.sum_win_pct / totals.win_count if totals.win_count else None
                    ),
                    avg_loss_pct=(
                        totals.sum_loss_pct / totals.loss_count if totals.loss_count else None
                    ),
                    profit_factor=(
                        totals.gross_win / totals.gross_loss if totals.gross_loss else None
                    ),
                    expectancy_pct=expectancy,
                    expectancy_per_bar_pct=per_bar,
                    avg_hold_bars=avg_hold,
                    forced_end_trades=totals.forced_end,
                    forced_end_share=(
                        totals.forced_end / totals.trade_count if totals.trade_count else None
                    ),
                    benchmark_expectancy_pct=baseline_pct,
                    benchmark_expectancy_per_bar_pct=baseline_per_bar,
                    excess_per_bar_pct=excess,
                    median_stock_expectancy_pct=median,
                    positive_stock_share=share,
                    positive_stock_min_trades=POSITIVE_STOCK_MIN_TRADES,
                    stock_denominator=len(counts),
                    verdict=verdict_for(totals.trade_count, expectancy),
                )
            )
        rows.sort(key=lambda item: (item.expectancy_pct is None, -(item.expectancy_pct or 0.0)))
        return rows

    def window(self) -> tuple[Optional[date], Optional[date]]:
        """各股首/末交易日的**中位数**——"典型股票覆盖了哪段时间"。"""
        if not self._first_days:
            return None, None
        return _median_day(self._first_days), _median_day(self._last_days)

    def bars_median(self) -> int:
        """中位股票有多少根 bar——**决定窗口有多长的是它**，不是平均值。

        均值会被 117 只有 250–499 根的股票拉高，读出来像是"覆盖了 2 年"。
        """
        return int(statistics.median(self._bar_counts)) if self._bar_counts else 0


def _median_day(days: list[date]) -> date:
    """交易日的"中位数"。

    不能用 `statistics.median`：偶数个样本时它会取中间两个的**平均**，而两个 `date`
    不能相加（线上 8 只演示股票就当场 TypeError，5,515 只真实股票是奇数才没炸）。
    取上中位数，它保证是一个**真实观测到的交易日**，而不是两个日期的虚构中点。
    """
    ordered = sorted(days)
    return ordered[len(ordered) // 2]
