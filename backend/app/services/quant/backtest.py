"""单标的策略回测引擎。

成交假设（A 股口径，全部显式写死以便复现）：
- **信号次日开盘成交**（`execution="next_open"`，默认）：信号在 bar i 收盘后产生、
  在 bar i+1 开盘成交，杜绝未来函数；`"close"` 只在对照/调试时使用。
- 多头满仓、无杠杆、按 100 股整手取整；现金不计息。
- 成本：佣金双边（含最低 5 元）、印花税仅卖出、滑点按不利方向作用在成交价上。
- **已知局限**：`daily_bars` 没有复权因子，除权除息日的跳空会被当成真实亏损；
  分红送转未建模。回测结果里必须把这条作为 caveat 展示给用户。

策略直接消费 `quant.signals` 的同一套信号（`timing` 策略复用 `decide_timing` 规则表），
保证"页面上看到的信号"与"回测里成交的信号"是同一份规则。
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from app.services.quant import signals as signal_rules
from app.services.quant.indicators import indicator_series
from app.services.quant.performance import parse_date, performance_metrics
from app.services.timing import decide_timing

__all__ = [
    "BASELINE_STRATEGY",
    "BacktestParams",
    "BacktestResult",
    "EquityPoint",
    "Expectancy",
    "LIVE_PARAMS",
    "POOL_CAVEAT",
    "Trade",
    "all_strategy_names",
    "backtest",
    "fused_strategy_names",
    "is_known_strategy",
    "strategy_names",
    "trade_expectancy",
    "verdict_for",
]

CAVEAT = "未复权：除权除息的跳空会被计为真实亏损，分红送转未建模；仅供研究参考。"

# 期望值随附的口径说明。最后一句不可省：实测回测是**满仓单标的**，而 /strategy 页的建议仓位
# 是 1% 风险预算缩仓后的结果，不写清楚页面会按仓位倍数夸大展示的净值曲线。
EXPECTANCY_CAVEAT = (
    f"{CAVEAT} 满仓单标的口径：期望/笔与期望/bar 与仓位无关、可直接参考；"
    "累计收益与最大回撤是本策略满仓跑出来的，与按 1% 风险预算缩仓后的结果不可比。"
)

# 期望值的样本门槛：低于该笔数时判 insufficient，**不论正负**。
# 2 年日线的融合策略只产生 ~10 笔成交，期望值的标准误 ≈ σ/√n，与均值同量级——
# 用 6 笔成交的正期望宣称"策略有效"，是这里必须堵死的失败模式。
INSUFFICIENT_TRADES = 20

# 全市场池化汇总的口径说明。逐条都是**池化新引入的**偏差，不写就是拿"20 万笔"当
# "20 万个独立观测"卖（而所有股票共享同一段行情，有效样本量远小于笔数）。
# 池化的口径说明，**逐条披露偏差**。刻意不用 `**粗体**` 标记：这段文字由接口原样返回、
# 前端直接落在页面上，而这一路上没有任何 markdown 渲染器——星号会原样显示出来。
POOL_CAVEAT = (
    f"{CAVEAT} 全市场池化口径：①生存偏差——库里只有当前上市的股票，已退市的缺席，"
    "结果系统性偏乐观；②成交不独立——所有股票共享同一段行情，20 万笔不等于 20 万个"
    "独立观测，有效样本量远小于笔数，故本页不给任何显著性结论；③按笔等权——"
    "历史更长的股票贡献更多笔，与「每只股票一票」不是一回事；④每只股票的末笔都是样本末尾"
    "强制平仓，不是规则出场（见 forced_end_share）；⑤汇总值不是任何一只股票的预期。"
)


def verdict_for(trade_count: int, expectancy_pct: Optional[float]) -> str:
    """样本量 + 符号 → `"positive" | "negative" | "insufficient"`。

    **唯一实现**。`trade_expectancy` 与全市场池化都必须走这里：池化凑不出 `BacktestResult`
    （20 万笔 `Trade` 对象在 1.6G 小内存机上放不下），只能自己聚合，于是判定规则很容易被
    抄成第二份——两份判定一旦漂移，同一个数字在 /strategy 与汇总页上会给出相反的结论。
    """
    if trade_count < INSUFFICIENT_TRADES:
        return "insufficient"
    if expectancy_pct is not None and expectancy_pct > 0:
        return "positive"
    return "negative"


@dataclass(frozen=True)
class BacktestParams:
    # A 股一手 100 股，100 万才买得起任意个股的一手（最贵的 ~3000 元/股 → 30 万/手）。
    # 早年这里默认 10 万，结果贵州茅台（~1500 元/股）一手要 15 万：`_enter` 算出 0 手后
    # 静默返回，所有策略净值恒等于初始资金、收益 0 —— 页面默认标的直接空跑。
    initial_cash: float = 1_000_000.0
    commission: float = 0.00025  # 佣金万 2.5，双边
    min_commission: float = 5.0  # 单笔最低 5 元
    stamp_tax: float = 0.0005  # 印花税万 5，仅卖出
    slippage: float = 0.0005  # 滑点万 5
    execution: str = "next_open"
    lot_size: int = 100
    stop_loss_atr: Optional[float] = None
    # 移动止盈（吊灯止损）：自建仓以来最高价回落 N×ATR 出场。None = 关闭，现有行为逐位不变。
    trail_atr: Optional[float] = None
    max_bars: int = 2500


@dataclass(frozen=True)
class Trade:
    entry_date: date
    entry_price: float
    shares: float
    exit_date: Optional[date]
    exit_price: Optional[float]
    pnl: float
    pnl_pct: float
    hold_bars: int
    exit_reason: str


@dataclass(frozen=True)
class EquityPoint:
    trade_date: date
    equity: float
    close: float
    position: float
    drawdown: float


@dataclass(frozen=True)
class BacktestResult:
    equity: list[EquityPoint]
    trades: list[Trade]
    metrics: dict[str, float | int | None]
    stats: dict[str, float | int | None]
    params: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Expectancy:
    """一个策略在一只标的上的**实测**记录。UI 的每个结论都必须挂着这个对象。

    这是本功能诚实政策的核心载体：不给"信号很强"这种无法验证的说法，只给"这条规则在
    这段历史上做了 n 笔、每笔平均赚亏多少"。`verdict` 是硬门槛，见 `trade_expectancy`。
    """

    strategy: str
    strategy_label: str
    window_days: int
    bars: int
    trade_count: int
    win_rate: Optional[float]  # 按**笔**（metrics.win_rate 是按日，同名不同义）
    avg_win_pct: Optional[float]
    avg_loss_pct: Optional[float]
    profit_factor: Optional[float]  # 按金额，与 _trade_stats.profit_factor 同口径
    expectancy_pct: Optional[float]  # 每笔期望收益率
    expectancy_per_bar_pct: Optional[float]  # 期望/持有 bar，防"5 天 1%"与"60 天 1%"混淆
    avg_hold_bars: Optional[float]
    forced_end_trades: int  # 样本末尾被强制平仓的笔数——不是规则触发的出场
    cumulative_return: Optional[float]
    max_drawdown: Optional[float]
    benchmark_return: Optional[float]  # 同期买入持有
    excess_return: Optional[float]  # 没有基准，页面会反向撒谎：跌 15% 的策略可能是在创造价值
    verdict: str  # "positive" | "negative" | "insufficient"
    verdict_text: str
    caveat: str


@dataclass(frozen=True)
class _Bar:
    trade_date: date
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class _OpenPosition:
    trade_date: date
    price: float
    shares: float


def _normalize(bars: Sequence[dict[str, object]]) -> list[_Bar]:
    out: list[_Bar] = []
    for row in bars:
        raw_date = row.get("trade_date") or row.get("date")
        day = parse_date(raw_date)
        if day is None:
            continue
        try:
            out.append(
                _Bar(
                    trade_date=day,
                    open=float(row["open"]),  # type: ignore[arg-type]
                    high=float(row["high"]),  # type: ignore[arg-type]
                    low=float(row["low"]),  # type: ignore[arg-type]
                    close=float(row["close"]),  # type: ignore[arg-type]
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda bar: bar.trade_date)
    return out


def _rule_strategy(names: tuple[str, ...]) -> Callable[[list[signal_rules.Signal]], str]:
    def _run(signals: list[signal_rules.Signal]) -> str:
        for signal in signals:
            if signal.name in names and signal.direction in {"buy", "sell"}:
                return signal.direction
        return "hold"

    return _run


def _timing_strategy(series: dict[str, list[float | None]], index: int) -> str:
    """复用 timing.decide_timing 的规则表：tone=buy 进场、reduce 离场。"""
    def _at(key: str) -> Optional[float]:
        values = series.get(key)
        if values is None or index >= len(values):
            return None
        return values[index]

    closes = series.get("close") or []
    price = closes[index] if index < len(closes) else None
    if price is None:
        return "hold"
    advice = decide_timing(price, _at("ma5"), _at("ma20"), _at("rsi"), _at("volatility"))
    if advice is None:
        return "hold"
    if advice["tone"] == "buy":
        return "buy"
    if advice["tone"] == "reduce":
        return "sell"
    return "hold"


_STRATEGY_RULES: dict[str, Callable[[list[signal_rules.Signal]], str]] = {
    "ma_cross": _rule_strategy(("ma_cross",)),
    "macd_cross": _rule_strategy(("macd_cross", "macd_hist_flip")),
    "kdj_cross": _rule_strategy(("kdj_cross",)),
    "boll_reversion": _rule_strategy(("boll_reversion",)),
    "donchian": _rule_strategy(("donchian",)),
    "trend_combo": _rule_strategy(("ma_cross", "macd_cross", "donchian")),
}
TIMING = "timing"
BUY_HOLD = "buy_hold"
# 不走规则表、在 _action 里单独分派的两个策略。
_BUILTIN_RULES = (TIMING, BUY_HOLD)

# 基准策略 = 买入持有。单一常量：`strategy_routes` 与池化都从这里取，不再各写一个字面量
# （两处各写一份，改一处漏一处就会让"超额收益"与"排行榜基准行"说的是两个东西）。
BASELINE_STRATEGY = BUY_HOLD

# 页面上的止损/移动止盈就是**产生那组实测期望的**止损/移动止盈。否则"实测"二字是假的：
# 用户会以为数字对应他看到的价位。计划端点、回测端点、全市场池化三处共用这一份。
LIVE_PARAMS = BacktestParams(stop_loss_atr=2.0, trail_atr=2.0)


def _fused_strategy(name: str) -> Callable[[list[signal_rules.Signal]], str]:
    """融合族策略：交给 signals.fused_action，与 /strategy 页展示的规则是同一份。"""

    def _run(signals: list[signal_rules.Signal]) -> str:
        return signal_rules.fused_action(name, signals)

    return _run


# 与 _STRATEGY_RULES **平行**的第二张注册表。不并进第一张，是因为 strategy_names() 的
# 8 个名字与顺序被 test_quant_backtest / test_quant_api 钉死，也是 /quant/backtest 对前端的
# 契约：把新策略塞进去会改掉既有端点的 response。新端点用 all_strategy_names() 拿 11 个。
_FUSED_RULES: dict[str, Callable[[list[signal_rules.Signal]], str]] = {
    name: _fused_strategy(name) for name in signal_rules.FUSED_STRATEGIES
}


def strategy_names() -> list[str]:
    """既有 8 个策略（/quant/backtest 的契约，顺序不可变）。"""
    return [*_STRATEGY_RULES, *_BUILTIN_RULES]


def fused_strategy_names() -> list[str]:
    return [*_FUSED_RULES]


def all_strategy_names() -> list[str]:
    """既有 8 个 + 融合 3 个 = 11，供 /strategy 端点使用。"""
    return [*strategy_names(), *fused_strategy_names()]


def is_known_strategy(name: str) -> bool:
    return name in _STRATEGY_RULES or name in _FUSED_RULES or name in _BUILTIN_RULES


def is_fused_strategy(name: str) -> bool:
    """融合策略读的是族分（固定分母），既有 8 个读的是各自那条规则——页面上要分开说。"""
    return name in _FUSED_RULES


STRATEGY_LABELS: dict[str, str] = {
    "ma_cross": "MA 金叉/死叉",
    "macd_cross": "MACD 金叉/死叉",
    "kdj_cross": "KDJ 交叉",
    "boll_reversion": "布林带回归",
    "donchian": "唐奇安通道突破",
    "trend_combo": "趋势三合一",
    "timing": "时机规则表",
    "buy_hold": "买入持有（基准）",
    "trend_follow": "趋势跟踪族",
    "mean_reversion": "均值回归族",
    "fusion": "多策略融合",
}


def strategy_label(name: str) -> str:
    return STRATEGY_LABELS.get(name, name)


def _commission(amount: float, params: BacktestParams) -> float:
    return max(amount * params.commission, params.min_commission)


def backtest(
    bars: Sequence[dict[str, object]],
    strategy: str = "ma_cross",
    params: BacktestParams | None = None,
) -> BacktestResult:
    """对单只标的执行回测，返回净值曲线、成交明细与绩效指标。"""
    params = params or BacktestParams()
    items = _normalize(bars)[: params.max_bars]
    if not is_known_strategy(strategy):
        raise ValueError(f"未知策略：{strategy}")
    if len(items) < 2:
        return BacktestResult(
            equity=[],
            trades=[],
            metrics=performance_metrics([]),
            stats=_trade_stats([]),
            params=_params_dict(params),
        )

    series = indicator_series(
        [
            {
                "close": bar.close,
                "high": bar.high,
                "low": bar.low,
                "open": bar.open,
            }
            for bar in items
        ]
    )
    series["close"] = [bar.close for bar in items]
    closes = [bar.close for bar in items]
    rule = _STRATEGY_RULES.get(strategy) or _FUSED_RULES.get(strategy)
    buy_hold = strategy == BUY_HOLD

    cash = params.initial_cash
    shares = 0.0
    entry_price = 0.0
    entry_index = 0
    # 建仓以来最高价（移动止盈的水位）与入场时冻结的 ATR。atr_at_entry 冻结而不是逐根取，
    # 是为了与 stop_level 同口径：整笔交易的止损/止盈宽度在入场那一刻就定死，可复现可解释。
    high_water = 0.0
    atr_at_entry: Optional[float] = None
    # 买不起一手而放弃的建仓次数。大于 0 且全程没开过仓，说明结果不可信（净值会是一条
    # 直线），必须显式告诉用户，不能让它看起来像"策略没触发信号"。
    unaffordable = 0
    stop_level: Optional[float] = None
    pending: Optional[str] = None
    open_trade: Optional[_OpenPosition] = None
    trades: list[Trade] = []
    equity: list[EquityPoint] = []
    peak = params.initial_cash

    def _enter(index: int, price: float) -> None:
        nonlocal cash, shares, entry_price, entry_index, stop_level, open_trade
        nonlocal unaffordable, high_water, atr_at_entry
        fill = price * (1 + params.slippage)
        budget = cash / (1 + params.commission)
        lots = math.floor(budget / (fill * params.lot_size))
        quantity = lots * params.lot_size
        if quantity <= 0:
            unaffordable += 1
            return
        amount = quantity * fill
        fee = _commission(amount, params)
        if amount + fee > cash:
            quantity -= params.lot_size
            if quantity <= 0:
                unaffordable += 1
                return
            amount = quantity * fill
            fee = _commission(amount, params)
        cash -= amount + fee
        shares = quantity
        entry_price = fill
        entry_index = index
        open_trade = _OpenPosition(
            trade_date=items[index].trade_date, price=fill, shares=quantity
        )
        atr_value = None
        if params.stop_loss_atr or params.trail_atr:
            atr_series = series.get("atr14") or []
            if index < len(atr_series) and atr_series[index] is not None:
                atr_value = atr_series[index]
        atr_at_entry = atr_value
        stop_level = (
            fill - params.stop_loss_atr * atr_value
            if params.stop_loss_atr and atr_value
            else None
        )
        high_water = fill

    def _exit(index: int, price: float, reason: str) -> None:
        nonlocal cash, shares, stop_level, open_trade, high_water, atr_at_entry
        if shares <= 0:
            return
        fill = price * (1 - params.slippage)
        amount = shares * fill
        fee = _commission(amount, params)
        cash += amount - fee - amount * params.stamp_tax
        if open_trade is not None:
            cost = open_trade.price * open_trade.shares
            pnl = amount - fee - amount * params.stamp_tax - cost
            trades.append(
                Trade(
                    entry_date=open_trade.trade_date,
                    entry_price=open_trade.price,
                    shares=open_trade.shares,
                    exit_date=items[index].trade_date,
                    exit_price=fill,
                    pnl=pnl,
                    pnl_pct=pnl / cost * 100 if cost else 0.0,
                    hold_bars=index - entry_index,
                    exit_reason=reason,
                )
            )
        shares = 0.0
        stop_level = None
        open_trade = None
        high_water = 0.0
        atr_at_entry = None

    for index, bar in enumerate(items):
        if buy_hold and shares <= 0:
            _enter(index, bar.open)

        if pending == "buy" and shares <= 0:
            _enter(index, bar.open)
        elif pending == "sell" and shares > 0:
            _exit(index, bar.open, "signal")
        pending = None

        # 固定止损与移动止盈合并成一次检查：两者都是"bar 内触及某价位即出场"，取更高的那条
        # （更近的保护位）先触发。合并成一个 exit_level 而不是连续两次 _exit，是为了让出场
        # 原因唯一、可解释——同根 bar 两个价位都破了时，报的是更高（更可能真的先成交）的那个。
        if shares > 0:
            exit_level: Optional[float] = None
            exit_reason = "stop"
            if stop_level is not None:
                exit_level = stop_level
            if params.trail_atr and atr_at_entry and high_water:
                trail_level = high_water - params.trail_atr * atr_at_entry
                if exit_level is None or trail_level >= exit_level:
                    exit_level, exit_reason = trail_level, "trail"
            if exit_level is not None and bar.low <= exit_level:
                _exit(index, min(bar.open, exit_level), exit_reason)

        # high_water 必须在这根 bar 的盘中检查**之后**更新（含建仓那根）。反过来写——先用
        # 当根最高价抬高水位、再拿当根最低价去测——就是同根 bar 的未来函数，会静默虚高所有
        # 带移动止盈的策略。test_quant_backtest 有一条专门钉死这个顺序的用例。
        if shares > 0:
            high_water = max(high_water, bar.high)

        if params.execution == "close":
            action = _action(strategy, rule, series, closes, index)
            if action == "buy" and shares <= 0:
                _enter(index, bar.close)
            elif action == "sell" and shares > 0:
                _exit(index, bar.close, "signal")
        elif index + 1 < len(items):
            action = _action(strategy, rule, series, closes, index)
            if (action == "buy" and shares <= 0) or (action == "sell" and shares > 0):
                pending = action

        value = cash + shares * bar.close
        peak = max(peak, value)
        equity.append(
            EquityPoint(
                trade_date=bar.trade_date,
                equity=round(value, 2),
                close=bar.close,
                position=1.0 if shares > 0 else 0.0,
                drawdown=round(value / peak - 1, 6) if peak else 0.0,
            )
        )

    if shares > 0:
        _exit(len(items) - 1, items[-1].close, "end")
        last = equity[-1]
        equity[-1] = EquityPoint(
            trade_date=last.trade_date,
            equity=round(cash, 2),
            close=last.close,
            position=0.0,
            drawdown=last.drawdown,
        )

    # 开过仓就必然会有平仓记录（末尾强制平仓），所以「没有任何成交」等价于「全程没建仓」。
    warning = None
    if unaffordable and not trades:
        probe = items[0]
        warning = (
            f"初始资金 {params.initial_cash:,.0f} 元买不起一手"
            f"（{probe.trade_date} 开盘 {probe.open:.2f} 元 × {params.lot_size} 股），"
            f"全程未能建仓（{unaffordable} 次建仓被资金拦下），净值曲线是一条直线；"
            "请调高 initial_cash。"
        )

    equity_values = [point.equity for point in equity]
    return BacktestResult(
        equity=equity,
        trades=trades,
        metrics=performance_metrics(equity_values, dates=[point.trade_date for point in equity]),
        stats=_trade_stats(trades),
        params=_params_dict(params, warning),
    )


def _action(
    strategy: str,
    rule: Callable[[list[signal_rules.Signal]], str] | None,
    series: dict[str, list[float | None]],
    closes: Sequence[float],
    index: int,
) -> str:
    if strategy == BUY_HOLD:
        return "hold"
    if rule is None:  # timing 策略
        return _timing_strategy(series, index)
    return rule(signal_rules.evaluate_signals(series, closes, index))


def _params_dict(params: BacktestParams, warning: str | None = None) -> dict[str, object]:
    return {
        "initial_cash": params.initial_cash,
        "commission": params.commission,
        "min_commission": params.min_commission,
        "stamp_tax": params.stamp_tax,
        "slippage": params.slippage,
        "execution": params.execution,
        "lot_size": params.lot_size,
        "stop_loss_atr": params.stop_loss_atr,
        "trail_atr": params.trail_atr,
        # 资金不足的警告拼在 caveat 后面：前端已经在渲染这个字段，不必再加新 UI 分支。
        "caveat": f"{CAVEAT} {warning}" if warning else CAVEAT,
    }


def _trade_stats(trades: Sequence[Trade]) -> dict[str, float | int | None]:
    if not trades:
        return {
            "trade_count": 0,
            "win_rate": None,
            "avg_hold_bars": None,
            "avg_win": None,
            "avg_loss": None,
            "profit_factor": None,
        }
    wins = [trade.pnl for trade in trades if trade.pnl > 0]
    losses = [trade.pnl for trade in trades if trade.pnl <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "trade_count": len(trades),
        "win_rate": len(wins) / len(trades),
        "avg_hold_bars": sum(trade.hold_bars for trade in trades) / len(trades),
        "avg_win": gross_win / len(wins) if wins else None,
        "avg_loss": -gross_loss / len(losses) if losses else None,
        "profit_factor": (gross_win / gross_loss) if gross_loss else None,
    }


def _metric(metrics: dict[str, float | int | None], key: str) -> Optional[float]:
    value = metrics.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def trade_expectancy(
    result: BacktestResult,
    *,
    strategy: str,
    window_days: int = 0,
    benchmark_return: Optional[float] = None,
) -> Expectancy:
    """把一次回测的成交明细折成"这条规则在这只标的上到底赚不赚钱"。

    `expectancy_pct = win_rate·avg_win_pct + (1-win_rate)·avg_loss_pct`，代数上等于
    `mean(trade.pnl_pct)`；用因子形式写是因为它就是教科书期望，也便于页面逐项解释。

    **`verdict` 是硬门槛**：`trade_count < INSUFFICIENT_TRADES` 一律 "insufficient"，
    不论正负。必须让"6 笔成交、正期望"**不可能**渲染成 "positive"——那正是本功能要防的
    自我欺骗。同理，负期望必须显式判 negative 并给出劝退文案。
    """
    trades = result.trades
    count = len(trades)
    bars = int(_metric(result.metrics, "bars") or 0)
    cumulative = _metric(result.metrics, "cumulative_return")
    drawdown = _metric(result.metrics, "max_drawdown")
    forced_end = sum(1 for trade in trades if trade.exit_reason == "end")

    win_rate = avg_win = avg_loss = expectancy = per_bar = avg_hold = None
    profit_factor = None
    if count:
        wins = [trade for trade in trades if trade.pnl > 0]
        losses = [trade for trade in trades if trade.pnl <= 0]
        win_rate = len(wins) / count
        avg_win = sum(trade.pnl_pct for trade in wins) / len(wins) if wins else 0.0
        avg_loss = sum(trade.pnl_pct for trade in losses) / len(losses) if losses else 0.0
        expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss
        avg_hold = sum(trade.hold_bars for trade in trades) / count
        gross_win = sum(trade.pnl for trade in wins)
        gross_loss = abs(sum(trade.pnl for trade in losses))
        profit_factor = (gross_win / gross_loss) if gross_loss else None
        if avg_hold:
            per_bar = expectancy / avg_hold

    excess = (
        cumulative - benchmark_return
        if cumulative is not None and benchmark_return is not None
        else None
    )

    verdict = verdict_for(count, expectancy)

    return Expectancy(
        strategy=strategy,
        strategy_label=strategy_label(strategy),
        window_days=window_days,
        bars=bars,
        trade_count=count,
        win_rate=win_rate,
        avg_win_pct=avg_win,
        avg_loss_pct=avg_loss,
        profit_factor=profit_factor,
        expectancy_pct=expectancy,
        expectancy_per_bar_pct=per_bar,
        avg_hold_bars=avg_hold,
        forced_end_trades=forced_end,
        cumulative_return=cumulative,
        max_drawdown=drawdown,
        benchmark_return=benchmark_return,
        excess_return=excess,
        verdict=verdict,
        verdict_text=_verdict_text(
            strategy, count, bars, win_rate, expectancy, benchmark_return, verdict
        ),
        caveat=EXPECTANCY_CAVEAT,
    )


def _verdict_text(
    strategy: str,
    count: int,
    bars: int,
    win_rate: Optional[float],
    expectancy: Optional[float],
    benchmark_return: Optional[float],
    verdict: str,
) -> str:
    label = strategy_label(strategy)
    if count == 0:
        return (
            f"「{label}」在这段历史上一次都没触发，没有任何可参考的实测记录；"
            "这不是策略稳健，是样本为空。"
        )
    head = f"该规则（{label}）在本标的最近 {bars} 个交易日实测 {count} 笔"
    if expectancy is None:
        return f"{head}，但样本量不足以下任何结论，勿据此下单。"
    if verdict == "insufficient":
        return (
            f"{head}，每笔期望 {expectancy:+.2f}%，但 {count} 笔低于 {INSUFFICIENT_TRADES} 笔的"
            "判断门槛——样本量不足以下任何结论，勿据此下单。"
        )
    if verdict == "negative":
        loss_pct = f"{(1 - win_rate) * 100:.0f}%" if win_rate is not None else "多数"
        return (
            f"{head}，{loss_pct} 的交易是亏的，每笔期望 {expectancy:+.2f}%；"
            "按此规则操作的历史结果是亏钱的，请勿据此下单。"
        )
    wins_pct = f"{win_rate * 100:.0f}%" if win_rate is not None else "多数"
    tail = (
        f"，同期买入持有 {benchmark_return * 100:+.2f}%"
        if benchmark_return is not None
        else ""
    )
    return (
        f"{head}，{wins_pct} 的交易是赚的，每笔期望 {expectancy:+.2f}%{tail}；"
        "历史样本不代表未来，请自行判断。"
    )
