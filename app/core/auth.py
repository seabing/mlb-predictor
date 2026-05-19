"""Authentication: the cookie-based app password gate and the admin password.

Two layers:
  1. `auth_token` cookie — anyone who knows APP_PASSWORD gets past /
  2. `admin_token` cookie — separate ADMIN_PASSWORD unlocks /admin

Visitor identification (/identify) is kept as an optional best-effort
tracking endpoint but is no longer required for access.

Exports `AuthMiddleware` (mounted on the app) and `login_router` (the
auth + identify + admin-auth endpoints).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
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

        # Visitor touch — best-effort, never blocks the request.
        visitor_id = request.cookies.get("visitor_id")
        if visitor_id:
            try:
                from app.visitors.services.store import visitor_store
                visitor_store.touch(
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
    """Accept either a plain HTML form POST or a JSON body.

    Form POST (from identify.html): returns 303 redirects so the browser
    handles cookie storage and navigation itself — no JS timing issues.

    JSON (legacy / API): returns JSON responses as before.
    """
    content_type = request.headers.get("content-type", "")
    is_json = "application/json" in content_type
    is_form = not is_json  # treat everything non-JSON as a form POST

    try:
        if is_form:
            form = await request.form()
            email = (str(form.get("email") or "")).strip().lower()
        else:
            body = await request.json()
            email = (body.get("email") or "").strip().lower()
    except Exception as parse_err:
        print(f"[identify] body parse failed: {parse_err}")
        if is_form:
            return RedirectResponse(url="/?e=save", status_code=303)
        return JSONResponse({"error": "Bad request"}, status_code=400)

    # Auth check
    if request.cookies.get("auth_token") != settings.app_password:
        if is_form:
            # Redirect to login — session has expired
            return RedirectResponse(url="/", status_code=303)
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    # Email validation
    if not email or "@" not in email or "." not in email.split("@")[-1]:
        if is_form:
            return RedirectResponse(url="/?e=email", status_code=303)
        return JSONResponse({"error": "Enter a valid email"}, status_code=400)

    # Register visitor — best-effort; never block access if the DB fails.
    tr