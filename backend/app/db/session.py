from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.models import Base

settings = get_settings()
settings.ensure_data_dir()
engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})


def configure_sqlite(target: Engine) -> None:
    """把与生产一致的 PRAGMA 挂到任意 engine 上。

    测试库必须用同一份配置。否则测试是在 SQLite 默认值下跑的（外键**不**强制），
    路由里漏掉的 `db.get(Stock, code)` 会在测试里静默通过、在生产变成 500——
    这正是 conftest 里两个 engine 都要调用本函数的原因。
    """

    @event.listens_for(target, "connect")
    def _set_sqlite_pragma(dbapi_connection: object, _: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA journal_mode=WAL")
        # 30s：调度器 news/quotes 与 API 写交错时偶发的 WAL 写锁竞争自动等待后重试，
        # 而非 5s 就抛 database is locked 吞掉一个 tick（读锁在 WAL 下不阻塞写者）。
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


configure_sqlite(engine)


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
