"""Minimal read-only API entrypoint for the local AMPgent UI.

This imports only the PostgreSQL-backed observer router. It deliberately does
not import the control-plane app or connect to Temporal.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from pepagent.api.observer import router as observer_router


app = FastAPI(title="AMPgent Observer API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)
app.include_router(observer_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "mode": "observer-only"}
