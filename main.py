from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from app.routes.mlb import router as mlb_router
from dotenv import load_dotenv
import os

load_dotenv()
APP_PASSWORD = os.getenv("APP_PASSWORD", "changeme")

app = FastAPI()

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    # Allow static files and the auth endpoint through
    if request.url.path.startswith("/static") or request.url.path == "/auth":
        return await call_next(request)

    # Check for auth cookie
    token = request.cookies.get("auth_token")
    if token != APP_PASSWORD:
        # Allow the root path to serve the login page
        if request.url.path == "/":
            return FileResponse("static/login.html")
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    return await call_next(request)

@app.post("/auth")
async def auth(request: Request, response: Response):
    body = await request.json()
    if body.get("password") == APP_PASSWORD:
        response = JSONResponse({"status": "ok"})
        response.set_cookie("auth_token", APP_PASSWORD, httponly=True)
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