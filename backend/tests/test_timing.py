from __future__ import annotations

from datetime import date, timedelta

import pytest
from app.db.models import DailyBar, Stock
from app.services.timing import compute_timing_for_codes, decide_timing


def _adv(price, ma5, ma20, rsi=None, vol=None):
    return decide_timing(price, ma5, ma20, rsi, vol)


@pytest.mark.parametrize(
    ("price", "ma5", "ma20", "rsi", "vol", "tone", "needle"),
    [
        # 超买 → 减仓
        (120.0, 118.0, 100.0, 82.0, None, "reduce", "超买"),
        # 上行但偏离 MA20 ≥10% → 不追高
        (120.0, 118.0, 100.0, 60.0, None, "watch", "不追高"),
        # 上行回踩至 MA20 下方 3% 内 → 分批买
        (99.0, 101.0, 100.0, 55.0, None, "buy", "回调MA20"),
        # 上行且贴近 MA20 → 现价可分批
        (101.5, 102.0, 100.0, 60.0, None, "buy", "现价贴近"),
        # 上行、温和偏离且 RSI 在良性带 → 沿 MA20 上行分批
        (106.0, 103.0, 100.0, 60.0, None, "buy", "沿MA20上行"),
        # 上行但 RSI 偏弱 → 持有待回踩
        (108.0, 105.0, 100.0, 40.0, None, "hold", "上行放缓"),
        # 上行但 RSI 偏热(68-78) → 持有
        (108.0, 105.0, 100.0, 72.0, None, "hold", "持有"),
        # 下方超卖、低波动 → 轻仓试探
        (95.0, 96.0, 100.0, 25.0, 3.0, "buy", "超卖"),
        # 深跌超卖 + 高波动 → 不接飞刀，回归观望
        (86.0, 95.0, 100.0, 25.0, 8.0, "watch", "观望"),
        # 均线下方 → 观望
        (90.0, 95.0, 100.0, 50.0, 3.0, "watch", "均线下方"),
        # 站上 MA20 但 5 日线走弱 → 观望
        (102.0, 98.0, 100.0, 50.0, None, "watch", "5日线走弱"),
    ],
)
def test_decide_timing_rules(price, ma5, ma20, rsi, vol, tone, needle) -> None:
    advice = _adv(price, ma5, ma20, rsi, vol)
    assert advice is not None
    assert advice["tone"] == tone
    assert needle in advice["label"]
    assert set(advice) == {"label", "tone", "detail"}
    assert advice["detail"].endswith(advice["label"])


@pytest.mark.parametrize(
    ("price", "ma5", "ma20"),
    [(None, 100.0, 100.0), (120.0, None, 100.0), (120.0, 100.0, None), (0.0, 100.0, 100.0)],
)
def test_decide_timing_returns_none_when_key_value_missing(price, ma5, ma20) -> None:
    assert decide_timing(price, ma5, ma20, 50.0, 3.0) is None


def _add_stock(session, ts_code: str) -> None:
    session.add(
        Stock(
            ts_code=ts_code,
            symbol=ts_code[:6],
            name="测试",
            industry="测试",
            market="SZ",
        )
    )
    # 必须先落库再插日线：daily_bars 的外键是真的（PRAGMA foreign_keys=ON 在生产与
    # 测试库上都生效），而两个 mapper 之间没有 relationship，同一次 flush 里
    # SQLAlchemy 不保证父表先写入。sync.py 也是同样的顺序（flush 股票 → 插日线）。
    session.flush()


def _add_bars(session, ts_code: str, n: int, base_close: float) -> None:
    start = date(2026, 1, 1)
    for i in range(n):
        session.add(
            DailyBar(
                ts_code=ts_code,
                trade_date=start + timedelta(days=i),
                close=base_close + i,
                open=base_close + i,
                high=base_close + i + 1,
                low=base_close + i - 1,
                pre_close=base_close + i - 1,
                pct_chg=1.0,
                volume=1_000_000.0,
                amount=10_000_000.0,
            )
        )
    session.flush()


def test_compute_timing_skips_series_below_20_bars(db_session) -> None:
    code = "600000.SZ"
    _add_stock(db_session, code)
    _add_bars(db_session, code, 10, 100.0)
    db_session.commit()

    result = compute_timing_for_codes(db_session, [code], {code: 110.0})
    assert result == {code: None}


def test_compute_timing_returns_label_for_full_series(db_session) -> None:
    code = "600000.SZ"
    _add_stock(db_session, code)
    _add_bars(db_session, code, 90, 100.0)
    db_session.commit()

    result = compute_timing_for_codes(db_session, [code], {code: 195.0})
    advice = result[code]
    assert advice is not None
    assert set(advice) == {"label", "tone", "detail"}
    assert advice["tone"] in {"buy", "hold", "reduce", "watch"}
