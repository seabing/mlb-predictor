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

        visitor_id = request.cookies.get("visitor_id")
        if not visitor_id:
            if path == "/":
                return FileResponse("static/identify.html")
            return JSONResponse({"error": "Identify required"}, status_code=401)

        # Best-effort touch of the visitor record; never block the request.
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
    is_form = "application/x-www-form-urlencoded" in content_type

    if is_form:
        form = await request.form()
        email = (str(form.get("email") or "")).strip().lower()
    else:
        body = await request.json()
        email = (body.get("email") or "").strip().lower()

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

    # Register visitor
    try:
        from app.visitors.services.store import visitor_store
        visitor_id = visitor_store.register(
            email,
            user_agent=request.headers.get("user-agent", ""),
            ip=_client_ip(request),
        )
    except Exception as e:
        print(f"[identify] register failed: {e}")
        if is_form:
            return RedirectResponse(url="/?e=save", status_code=303)
        return JSONResponse({"error": f"Failed to save: {e}"}, status_code=500)

    # Success — set cookie
    cookie_kwargs = dict(httponly=True, samesite="lax", max_age=60 * 60 * 24 * 365)
    if is_form:
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie("visitor_id", visitor_id, **cookie_kwargs)
        return response
    response = JSONResponse({"status": "ok"})
    response.set_cookie("visitor_id", visitor_id, **cookie_kwargs)
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
