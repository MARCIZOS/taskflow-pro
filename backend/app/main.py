from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm.exc import StaleDataError

from .api import router
from .config import get_settings
from .db import Base, SessionLocal, engine
from .services.board import ServiceError

log = logging.getLogger("taskflow")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(engine)
    if get_settings().seed_on_startup:
        from .seed import seed

        with SessionLocal() as session:
            seed(session, reset=False)
    yield


app = FastAPI(title="TaskFlow Pro", version="1.0.0", lifespan=lifespan)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type"],
)
app.include_router(router)


@app.exception_handler(ServiceError)
async def _service_error(_req: Request, exc: ServiceError):
    return JSONResponse(status_code=exc.status, content={"error": exc.to_dict()})


@app.exception_handler(StaleDataError)
async def _stale(_req: Request, _exc: StaleDataError):
    return JSONResponse(
        status_code=409,
        content={"error": {"code": "stale_version", "message": "This task was changed elsewhere. Please retry."}},
    )


@app.exception_handler(RequestValidationError)
async def _invalid(_req: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(p) for p in first.get("loc", [])[1:])
    msg = first.get("msg", "Invalid request").removeprefix("Value error, ")
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "invalid_request", "message": f"{field}: {msg}" if field else msg}},
    )


@app.exception_handler(Exception)
async def _unexpected(_req: Request, exc: Exception):
    log.exception("Unhandled error", exc_info=exc)
    return JSONResponse(status_code=500, content={"error": {"code": "internal", "message": "Something went wrong."}})


# Serve the built React app from the same origin when it exists, so a single
# container can host the whole product.
_dist = Path(settings.frontend_dist).resolve()
if (_dist / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa(full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"error": {"code": "not_found", "message": "Unknown API route"}})
        candidate = (_dist / full_path).resolve()
        if full_path and candidate.is_file() and _dist in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(_dist / "index.html")
