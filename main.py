"""FastAPI application entry point.

Responsibilities here are deliberately narrow:
  - Construct the FastAPI app
  - Mount auth middleware + login router
  - Include feature routers
  - Manage the auto-predict background loop via lifespan
  - Serve a couple of top-level static pages (/, /admin)

Everything else lives in a feature folder under app/.
"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.auth import AuthMiddleware, admin_authorized, login_router
from app.core.config import settings
from app.mlb.routes import router as mlb_data_router
from app.predictions.routes import router as predictions_router
from app.routes.mlb import router as legacy_router
from app.scheduler.routes import router as scheduler_router
from app.scheduler.services.auto_predict import scheduler as auto_predict_scheduler
from app.tuning.routes import router as tuning_router


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

# Auth + identify + admin-login endpoints
app.include_router(login_router)

# Feature routes — each step of the refactor moves more endpoints here
app.include_router(mlb_data_router, prefix="/api")
app.include_router(predictions_router, prefix="/api")
app.include_router(tuning_router, prefix="/api")
app.include_router(scheduler_router, prefix="/api")
# Legacy router still holds: salaries, trades. Final cleanup in step 6.
app.include_router(legacy_router, prefix="/api")


# ---------------------------------------------------------------------------
# Top-level pages and admin endpoints (admin endpoints move to visitors/ later)
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    return FileResponse("static/index.html")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/admin")
def admin_page():
    return FileResponse("static/admin.html")


@app.get("/api/admin/visitors")
def admin_visitors(request: Request):
    if not admin_authorized(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    from app.services.visitors import summary
    return summary()


app.mount("/static", StaticFiles(directory="static"), name="static")
