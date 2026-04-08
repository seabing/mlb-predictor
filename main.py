from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from app.routes.mlb import router as mlb_router
from dotenv import load_dotenv
import os

load_dotenv()

app = FastAPI()

app.include_router(mlb_router, prefix="/api")

@app.get("/")
def root():
    return FileResponse("static/index.html")

@app.get("/health")
def health():
    return {"status": "ok"}

app.mount("/static", StaticFiles(directory="static"), name="static")