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
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", APP_PASSWORD + "-admin")
AUTO_PREDICT_ENABLED = os.getenv("AUTO_PREDICT_ENABLED", "1") not in ("0", "false", "False", "")

PUBLIC_PATHS = {"/auth", "/identify", "/health", "/admin", "/admin/auth"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # Allow static and admin assets
        if path in PUBLIC_PATHS or path.startswith("/static") or path.startswith("/api/admin"):
            return await call_next(request)
        token = request.cookies.get("auth_token")
        if token != APP_PASSWORD:
            if path == "/":
                return FileResponse("static/login.html")
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        # Authenticated — make sure we know who they are
        visitor_id = request.cookies.get("visitor_id")
        if not visitor_id:
            if path == "/":
                return FileResponse("static/identify.html")
            return JSONResponse({"error": "Identify required"}, status_code=401)
        # Touch the visitor record (best-effort, never block the request)
        try:
            from app.services.visitors import touch
            ua = request.headers.get("user-agent", "")
            ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
                  or (request.client.host if request.client else ""))
            touch(visitor_id, path=path, user_agent=ua, ip=ip)
        except Exception as e:
            print(f"[visitor.touch] {e}")
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
        response.set_cookie(
            "auth_token", APP_PASSWORD, httponly=True, samesite="lax",
            max_age=60 * 60 * 24 * 365,
        )
        return response
    return JSONResponse({"error": "Wrong password"}, status_code=401)


@app.post("/identify")
async def identify(request: Request):
    # Caller must already have the auth cookie
    if request.cookies.get("auth_token") != APP_PASSWORD:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    if not email or "@" not in email or "." not in email.split("@")[-1]:
        return JSONResponse({"error": "Enter a valid email"}, status_code=400)
    try:
        from app.services.visitors import register
        ua = request.headers.get("user-agent", "")
        ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
              or (request.client.host if request.client else ""))
        visitor_id = register(email, user_agent=ua, ip=ip)
    except Exception as e:
        return JSONResponse({"error": f"Failed to save: {e}"}, status_code=500)
    response = JSONResponse({"status": "ok"})
    response.set_cookie(
        "visitor_id", visitor_id, httponly=True, samesite="lax",
        max_age=60 * 60 * 24 * 365,
    )
    return response


@app.get("/admin")
def admin_page():
    return FileResponse("static/admin.html")


@app.post("/admin/auth")
async def admin_auth(request: Request):
    body = await request.json()
    if body.get("password") == ADMIN_PASSWORD:
        response = JSONResponse({"status": "ok"})
        response.set_cookie(
            "admin_token", ADMIN_PASSWORD, httponly=True, samesite="lax",
            max_age=60 * 60 * 24 * 30,
        )
        return response
    return JSONResponse({"error": "Wrong password"}, status_code=401)


@app.get("/api/admin/visitors")
def admin_visitors(request: Request):
    if request.cookies.get("admin_token") != ADMIN_PASSWORD:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    from app.services.visitors import summary
    return summary()

app.include_router(mlb_router, prefix="/api")

@app.get("/")
def root():
    return FileResponse("static/index.html")

@app.get("/health")
def health():
    return {"status": "ok"}

app.mount("/static", StaticFiles(directory="static"), name="static")