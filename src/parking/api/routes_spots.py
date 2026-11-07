import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import AwareDatetime

from parking.llm.tools import Search
from parking.serve.availability import find_parking
from parking.serve.store import Store

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    path = Path(os.getenv("PARKING_DB_PATH", "data/serving/parking.duckdb"))
    app.state.store = None
    if path.is_file():
        try:
            app.state.store = Store(path)
        except (duckdb.Error, OSError, ValueError):
            log.exception("Could not load parking database %s", path)
    yield


router = APIRouter(lifespan=lifespan)


def require_store(request: Request):
    if request.app.state.store is None:
        raise HTTPException(503, "Parking data is unavailable.")
    return request.app.state.store


LoadedStore = Annotated[Store, Depends(require_store)]


@router.get("/health")
def health(request: Request):
    return {"status": "ok", "serving_db_loaded": request.app.state.store is not None}


@router.get("/v1/meta")
def metadata(store: LoadedStore):
    return store.metadata


@router.get("/v1/meters/{pole_id}")
def details(pole_id: str, store: LoadedStore):
    if pole_id not in store.by_id:
        raise HTTPException(404, "Unknown meter ID.")
    return store.details(pole_id)


@router.get("/v1/spots")
def spots(
    store: LoadedStore,
    lat: Annotated[float, Query(ge=32.5, le=33.3)],
    lon: Annotated[float, Query(ge=-117.6, le=-116.8)],
    arrival_time: AwareDatetime | None = None,
    duration_minutes: Annotated[int | None, Query(gt=0)] = None,
    radius_m: int = 400,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
):
    return find_parking(
        store,
        Search(
            lat=lat,
            lon=lon,
            arrival_time=arrival_time,
            duration_minutes=duration_minutes,
            radius_m=radius_m,
            max_results=limit,
        ),
    )
