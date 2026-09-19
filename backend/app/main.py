from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.notify_routes import notify_router
from app.api.quant_routes import quant_router
from app.api.routes import router
from app.api.strategy_routes import strategy_router
from app.core.config import get_settings
from app.db.session import SessionLocal, init_db
from app.providers.base import ProviderError
from app.scheduler import create_scheduler, start_startup_compensation
from app.services.sync import seed_demo


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    settings = get_settings()
    if settings.use_mock_data:
        with SessionLocal() as session:
            seed_demo(session)
    scheduler = create_scheduler() if settings.enable_scheduler else None
    if scheduler is not None:
        scheduler.start()
    start_startup_compensation()
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)


settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_host_list)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)
app.include_router(router)
app.include_router(quant_router)
app.include_router(strategy_router)
app.include_router(notify_router)


@app.exception_handler(ProviderError)
async def provider_error_handler(_: Request, exc: ProviderError) -> JSONResponse:
    return JSONResponse(
        status_code=502, content={"detail": f"数据源异常：{exc}"}
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(_: Request, _exc: Exception) -> JSONResponse:
    # HTTPException / 校验错误由 FastAPI 默认处理器接管，不会走到这里
    return JSONResponse(status_code=500, content={"detail": "服务内部错误，请查看后端日志"})


frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="接口不存在")
        candidate = (frontend_dist / path).resolve()
        try:
            candidate.relative_to(frontend_dist.resolve())
        except ValueError:
            raise HTTPException(status_code=404, detail="资源不存在") from None
        return FileResponse(candidate if candidate.is_file() else frontend_dist / "index.html")
