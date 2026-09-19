from __future__ import annotations

import os
import tempfile

import pytest
from app.db.models import Base
from app.providers.mock import MockProvider
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture(scope="session", autouse=True)
def _isolate_session_db():
    """把所有测试（含现有 test_core 的 TestClient）重定向到一次性临时库。

    注意 main/scheduler 在 import 时以 ``from app.db.session import SessionLocal``
    的方式绑定工厂对象，因此这里需要一并替换这些模块里已绑定的名字，而不是
    只改 app.db.session 上的属性。
    """
    import app.db.session as db_session_mod
    import app.main as main_mod
    import app.scheduler as scheduler_mod
    import app.services.pool as pool_mod

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    # 与生产同一份 PRAGMA（含 foreign_keys=ON）。不加这句，测试库跑在 SQLite 默认值下，
    # 外键不被强制，路由里漏掉的 db.get(Stock, code) 会静默通过、只在生产 500。
    db_session_mod.configure_sqlite(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    originals = (
        db_session_mod.engine,
        db_session_mod.SessionLocal,
        main_mod.SessionLocal,
        main_mod.create_scheduler,
        main_mod.start_startup_compensation,
        scheduler_mod.SessionLocal,
        pool_mod.SessionLocal,
    )
    db_session_mod.engine = engine
    db_session_mod.SessionLocal = factory
    main_mod.SessionLocal = factory
    main_mod.create_scheduler = lambda: None  # type: ignore[assignment]
    main_mod.start_startup_compensation = lambda: None  # type: ignore[assignment]
    scheduler_mod.SessionLocal = factory
    # 池化任务自己开会话（计算会话与 SyncRun 行刻意分开，见 services/pool.py），
    # 漏掉这一处，测试会直接往真实库写 SyncRun / strategy_pool_stats。
    pool_mod.SessionLocal = factory
    yield
    (
        db_session_mod.engine,
        db_session_mod.SessionLocal,
        main_mod.SessionLocal,
        main_mod.create_scheduler,
        main_mod.start_startup_compensation,
        scheduler_mod.SessionLocal,
        pool_mod.SessionLocal,
    ) = originals
    engine.dispose()
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture()
def engine(tmp_path):
    url = f"sqlite:///{tmp_path / 'test.db'}"
    import app.db.session as db_session_mod

    test_engine = create_engine(url, connect_args={"check_same_thread": False})
    db_session_mod.configure_sqlite(test_engine)
    Base.metadata.create_all(test_engine)
    yield test_engine
    test_engine.dispose()


@pytest.fixture()
def db_session(engine):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session


@pytest.fixture()
def mock_provider(monkeypatch):
    """把 sync/routes 用到的 get_provider 钉死到 MockProvider，屏蔽本机环境变量。"""
    import app.api.bars as bars_mod
    import app.api.routes as routes_mod
    import app.services.history as history_mod
    import app.services.sync as sync_mod

    provider = MockProvider()
    monkeypatch.setattr(sync_mod, "get_provider", lambda: provider)
    monkeypatch.setattr(routes_mod, "get_provider", lambda: provider)
    # 按需回补走的是 history 模块自己绑定的名字，不补这一处就会打到真实 TuShare
    monkeypatch.setattr(history_mod, "get_provider", lambda: provider)
    # 指数基准同理：`benchmark()` 从 quant_routes 搬到了 api/bars，绑定也跟着搬——
    # 列表补的是模块里那个名字，不是 `app.services.sync` 上的属性。
    monkeypatch.setattr(bars_mod, "get_provider", lambda: provider)
    return provider


@pytest.fixture()
def client(engine, monkeypatch, mock_provider):
    """隔离的 TestClient：lifespan 会在临时库上 seed_demo，且不启动调度器。"""
    import app.db.session as db_session_mod
    import app.main as main_mod
    import app.services.pool as pool_mod
    from fastapi.testclient import TestClient

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(db_session_mod, "engine", engine)
    monkeypatch.setattr(db_session_mod, "SessionLocal", factory)
    monkeypatch.setattr(main_mod, "SessionLocal", factory)
    monkeypatch.setattr(main_mod, "create_scheduler", lambda: None)
    monkeypatch.setattr(main_mod, "start_startup_compensation", lambda: None)
    # 池化任务自己的会话工厂：`POST /strategy/pool/refresh` 会经由它开新会话，
    # 不钉住就会打到真实库（这个端点在测试里是被直接调用的）。
    monkeypatch.setattr(pool_mod, "SessionLocal", factory)
    with TestClient(main_mod.app, base_url="http://localhost") as test_client:
        yield test_client
