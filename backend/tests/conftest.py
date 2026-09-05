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

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    originals = (
        db_session_mod.engine,
        db_session_mod.SessionLocal,
        main_mod.SessionLocal,
        main_mod.create_scheduler,
        main_mod.start_startup_compensation,
        scheduler_mod.SessionLocal,
    )
    db_session_mod.engine = engine
    db_session_mod.SessionLocal = factory
    main_mod.SessionLocal = factory
    main_mod.create_scheduler = lambda: None  # type: ignore[assignment]
    main_mod.start_startup_compensation = lambda: None  # type: ignore[assignment]
    scheduler_mod.SessionLocal = factory
    yield
    (
        db_session_mod.engine,
        db_session_mod.SessionLocal,
        main_mod.SessionLocal,
        main_mod.create_scheduler,
        main_mod.start_startup_compensation,
        scheduler_mod.SessionLocal,
    ) = originals
    engine.dispose()
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture()
def engine(tmp_path):
    url = f"sqlite:///{tmp_path / 'test.db'}"
    test_engine = create_engine(url, connect_args={"check_same_thread": False})
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
    import app.api.routes as routes_mod
    import app.services.sync as sync_mod

    provider = MockProvider()
    monkeypatch.setattr(sync_mod, "get_provider", lambda: provider)
    monkeypatch.setattr(routes_mod, "get_provider", lambda: provider)
    return provider


@pytest.fixture()
def client(engine, monkeypatch, mock_provider):
    """隔离的 TestClient：lifespan 会在临时库上 seed_demo，且不启动调度器。"""
    import app.db.session as db_session_mod
    import app.main as main_mod
    from fastapi.testclient import TestClient

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(db_session_mod, "engine", engine)
    monkeypatch.setattr(db_session_mod, "SessionLocal", factory)
    monkeypatch.setattr(main_mod, "SessionLocal", factory)
    monkeypatch.setattr(main_mod, "create_scheduler", lambda: None)
    monkeypatch.setattr(main_mod, "start_startup_compensation", lambda: None)
    with TestClient(main_mod.app, base_url="http://localhost") as test_client:
        yield test_client
