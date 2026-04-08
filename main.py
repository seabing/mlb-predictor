from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.base import BaseHTTPMiddleware
from app.routes.mlb import router as mlb_router
from dotenv import load_dotenv
import os

load_dotenv()
APP_PASSWORD = os.getenv("APP_PASSWORD", "changeme")

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

app = FastAPI()
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