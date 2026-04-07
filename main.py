from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from app.routes.mlb import router as mlb_router

app = FastAPI()

app.include_router(mlb_router)
@app.get("/api/test")
def test():
    return {"test": "working"}

@app.get("/")
def root():
    return FileResponse("static/index.html")

@app.get("/health")
def health():
    return {"status": "ok"}

app.mount("/static", StaticFiles(directory="static"), name="static")