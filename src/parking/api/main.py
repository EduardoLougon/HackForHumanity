"""FastAPI Application Main Entrypoint."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.parking.api.routes_chat import router as chat_router
from src.parking.api.routes_spots import router as spots_router
from src.parking.serve.store import store

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("parking_api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context loading store on startup."""
    logger.info("Initializing San Diego Parking Store...")
    try:
        store.load()
        logger.info(f"Store loaded successfully ({len(store.meters_by_id):,} meters).")
    except Exception as e:
        logger.error(f"Failed to initialize parking store: {e}")
    yield
    if store.con:
        store.con.close()
        logger.info("Store connection closed.")


app = FastAPI(
    title="San Diego Parking Finder API",
    description="Deterministic parking spot ranking and LLM chat assistant powered by historical meter payment profiles.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS setup
cors_origins_raw = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173,*")
origins = [o.strip() for o in cors_origins_raw.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if "*" not in origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routes
app.include_router(spots_router)
app.include_router(chat_router)


@app.get("/")
def root():
    return {
        "service": "San Diego Parking Finder API",
        "docs_url": "/docs",
        "health_url": "/health",
        "spots_url": "/v1/spots",
        "version": "1.0.0",
    }
