from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import db as db_module
from .seed import seed_defaults
from .v2 import router as v2_router
from .v2.seed import seed_defaults_v2


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_module.init_db()
    db = db_module.SessionLocal()
    try:
        seed_defaults(db)
        seed_defaults_v2(db)
    finally:
        db.close()
    yield


app = FastAPI(title="Smart Finance Manager", version="1.0.0", lifespan=lifespan)

app.include_router(v2_router)


@app.get("/")
def root() -> dict:
    return {"message": "Financer V2 Backend"}
