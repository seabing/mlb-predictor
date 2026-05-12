"""Authentication: the cookie-based app password gate, the visitor email
gate, and the separate admin password.

Three layers:
  1. `auth_token` cookie — anyone who knows APP_PASSWORD gets past /
  2. `visitor_id` cookie — every authed user must identify with an email
  3. `admin_token` cookie — separate ADMIN_PASSWORD unlocks /admin

Exports `AuthMiddleware` (mounted on the app) and `login_router` (the
auth + identify + admin-auth endpoints).
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings

PUBLIC_PATHS = {"/auth", "/identify", "/health", "/admin", "/admin/auth"}


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


class AuthMiddleware(BaseHTTPMiddleware):
    """Cookie auth + visitor identification + per-request touch logging.

    Anything in PUBLIC_PATHS / /static / /api/admin is bypassed (admin
    endpoints handle their own auth).
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if (
            path in PUBLIC_PATHS
            or path.startswith("/static")
            or path.startswith("/api/admin")
        ):
            return await call_next(request)

        token = request.cookies.get("auth_token")
        if token != settings.app_password:
            if path == "/":
                return FileResponse("static/login.html")
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        visitor_id = request.cookies.get("visitor_id")
        if not visitor_id:
            if path == "/":
                return FileResponse("static/identify.html")
            return JSONResponse({"error": "Identify required"}, status_code=401)

        # Best-effort touch of the visitor record; never block the request.
        try:
            from app.services.visitors import touch  # moved later in refactor
            touch(
                visitor_id,
                path=path,
                user_agent=request.headers.get("user-agent", ""),
                ip=_client_ip(request),
            )
        except Exception as e:  # pragma: no cover
            print(f"[visitor.touch] {e}")

        return await call_next(request)


def admin_authorized(request: Request) -> bool:
    """True iff the request carries a valid admin_token cookie."""
    return request.cookies.get("admin_token") == settings.admin_password


login_router = APIRouter()


@login_router.post("/auth")
async def auth(request: Request):
    body = await request.json()
    if body.get("password") == settings.app_password:
        response = JSONResponse({"status": "ok"})
        response.set_cookie(
            "auth_token",
            settings.app_password,
            httponly=True,
            samesite="lax",
            max_age=60 * 60 * 24 * 365,
        )
        return response
    return JSONResponse({"error": "Wrong password"}, status_code=401)


@login_router.post("/identify")
async def identify(request: Request):
    # Caller must already have the app-password cookie.
    if request.cookies.get("auth_token") != settings.app_password:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    if not email or "@" not in email or "." not in email.split("@")[-1]:
        return JSONResponse({"error": "Enter a valid email"}, status_code=400)
    try:
        from app.services.visitors import register  # moved later in refactor
        visitor_id = register(
            email,
            user_agent=request.headers.get("user-agent", ""),
            ip=_client_ip(request),
        )
    except Exception as e:
        return JSONResponse({"error": f"Failed to save: {e}"}, status_code=500)
    response = JSONResponse({"status": "ok"})
    response.set_cookie(
        "visitor_id",
        visitor_id,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 365,
    )
    return response


@login_router.post("/admin/auth")
async def admin_auth(request: Request):
    body = await request.json()
    if body.get("password") == settings.admin_password:
        response = JSONResponse({"status": "ok"})
        response.set_cookie(
            "admin_token",
            settings.admin_password,
            httponly=True,
            samesite="lax",
            max_age=60 * 60 * 24 * 30,
        )
        return response
    return JSONResponse({"error": "Wrong password"}, status_code=401)
