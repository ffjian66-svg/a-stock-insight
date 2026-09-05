from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import FundamentalSnapshot

# 快照中的估值 / 财务质量 / 负债指标口径
SNAPSHOT_METRIC_COLUMNS = (
    "pe_ttm",
    "pb",
    "total_mv",
    "turnover_rate",
    "roe",
    "revenue_growth",
    "profit_growth",
    "debt_ratio",
)


def snapshot_metrics(snapshots: list[FundamentalSnapshot]) -> dict[str, float | None]:
    """每项指标取快照序列中最新有值的一期。

    估值类指标落在最新交易日（daily_basic）快照，质量/负债类指标落在最新
    报告期（fina_indicator）快照，二者混排在按 trade_date 倒序的序列里时，
    本函数按列各自取"最新有值"，从而拼出一份可用于评分的基本面画像。
    """
    result: dict[str, float | None] = {}
    for column in SNAPSHOT_METRIC_COLUMNS:
        value: float | None = None
        for snapshot in snapshots:
            candidate = getattr(snapshot, column)
            if candidate is not None:
                value = float(candidate)
                break
        result[column] = value
    return result


def latest_snapshot_metrics(session: Session, ts_code: str) -> dict[str, float | None]:
    snapshots = list(
        session.scalars(
            select(FundamentalSnapshot)
            .where(FundamentalSnapshot.ts_code == ts_code)
            .order_by(FundamentalSnapshot.trade_date.desc())
        ).all()
    )
    return snapshot_metrics(snapshots)
