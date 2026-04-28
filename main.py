import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from app.routes.mlb import router as mlb_router
from app.services.scheduler import auto_predict_loop
from dotenv import load_dotenv

load_dotenv()
APP_PASSWORD = os.getenv("APP_PASSWORD", "changeme")
AUTO_PREDICT_ENABLED = os.getenv("AUTO_PREDICT_ENABLED", "1") not in ("0", "false", "False", "")

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in ["/auth", "/health"] or request.url.path.startswith("/static"):
            return await call_next(request)
        token = request.cookies.get("auth_token")
        if token != APP_PASSWORD:
            if request.url.path == "/":
                return FileResponse("static/login.html")
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await call_next(request)

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = None
    if AUTO_PREDICT_ENABLED:
        task = asyncio.create_task(auto_predict_loop())
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

@app.post("/auth")
async def auth(request: Request):
    body = await request.json()
    if body.get("password") == APP_PASSWORD:
        response = JSONResponse({"status": "ok"})
        response.set_cookie("auth_token", APP_PASSWORD, httponly=True, samesite="lax")
        return response
    return JSONResponse({"error": "Wrong password"}, status_code=401)

app.include_router(mlb_router, prefix="/api")

@app.get("/")
def root():
    return FileResponse("static/index.html")

@app.get("/health")
def health():
    return {"status": "ok"}

app.mount("/static", StaticFiles(directory="static"), name="static")