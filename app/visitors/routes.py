"""Visitor + admin routes.

Note: the email-gate endpoint /identify lives in core.auth alongside /auth,
because it's part of the auth flow (sets a cookie). The admin views live
here because they read visitor data.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from app.core.auth import admin_authorized
from app.visitors.services.store import visitor_store

router = APIRouter()


# ---------- admin dashboard page (top-level, no /api prefix) ----------
admin_pages = APIRouter()


@admin_pages.get("/admin")
def admin_page():
    return FileResponse("static/admin.html")


# ---------- admin API ----------

@router.get("/admin/visitors")
def admin_visitors(request: Request):
    if not admin_authorized(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    return visitor_store.summary()
