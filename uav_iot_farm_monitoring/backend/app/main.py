from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .database import Base, engine
from .routers import router

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Farm IoT API", version="0.1.0")
app.include_router(router, prefix="/api/v1")

frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
app.mount("/static", StaticFiles(directory=frontend_dir), name="static")


@app.get("/", include_in_schema=False)
def web_app():
    return FileResponse(frontend_dir / "index.html")
