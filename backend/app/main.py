"""App entry point.

API-only: the Angular bundle ships in its own nginx image, which also
reverse-proxies /api and /ws here (see ``frontend/nginx.conf``). The SPA
deep-link fallback therefore lives in that nginx config, not in this process.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import db
from .api import router
from .config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    yield
    await db.reset_engines()


app = FastAPI(title="Japanese Conversation Practice", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
