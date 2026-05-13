"""FastAPI application entry point.

Responsibilities here are deliberately narrow:
  - Construct the FastAPI app
  - Mount auth middleware + login router
  - Include feature routers
  - Manage the auto-predict background loop via lifespan
  - Serve top-level static pages (/, /health)

Everything else lives in a feature folder under app/.
"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.auth import AuthMiddleware, login_router
from app.core.config import settings
from app.mlb.routes import router as mlb_data_router
from app.predictions.routes import router as predictions_router
from app.salaries.routes import router as salaries_router
from app.scheduler.routes import router as scheduler_router
from app.scheduler.services.auto_predict import scheduler as auto_predict_scheduler
from app.trades.routes import router as trades_router
from app.tuning.routes import router as tuning_router
from app.visitors.routes import admin_pages, router as visitors_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = None
    if settings.auto_predict_enabled:
        task = asyncio.create_task(auto_predict_scheduler.run_loop())
        print("[startup] auto-predict scheduler started")
    else:
        print("[startup] auto-predict scheduler disabled via env")
    try:
        yield
    finally:
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            print("[shutdown] auto-predict scheduler stopped")


app = FastAPI(lifespan=lifespan)
app.add_middleware(AuthMiddleware)

# Top-level auth + admin static pages (no /api prefix)
app.include_router(login_router)
app.include_router(admin_pages)

# Feature API routers
app.include_router(mlb_data_router, prefix="/api")
app.include_router(predictions_router, prefix="/api")
app.include_router(tuning_router, prefix="/api")
app.include_router(scheduler_router, prefix="/api")
app.include_router(trades_router, prefix="/api")
app.include_router(salaries_router, prefix="/api")
app.include_router(visitors_router, prefix="/api")


@app.get("/")
def root():
    return FileResponse("static/index.html")


@app.get("/health")
def health():
    return {"status": "ok"}


app.mount("/static", StaticFiles(directory="static"), name="static")
