import os
from contextlib import asynccontextmanager
from functools import partial
from threading import Lock

import httpx
from cachetools import TTLCache
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Request
from google import genai
from google.genai import errors, types

from parking.geo.geocode import GoogleGeocoder, ServiceUnavailable
from parking.llm.gemini_client import ChatRequest, chat
from parking.llm.tools import ParkingService


@asynccontextmanager
async def lifespan(app):
    load_dotenv()
    app.state.chat_counts = TTLCache(maxsize=10000, ttl=60)
    app.state.chat_lock = Lock()
    with (
        httpx.Client(timeout=8) as geo_http,
        httpx.Client(
            base_url=os.getenv("PARKING_API_BASE_URL", "http://127.0.0.1:8000"),
            timeout=8,
        ) as parking_http,
    ):
        app.state.geocoder = GoogleGeocoder(
            geo_http, os.getenv("GOOGLE_GEOCODING_API_KEY", "")
        )
        app.state.parking = ParkingService(parking_http)
        key, model = os.getenv("GEMINI_API_KEY"), os.getenv("GEMINI_MODEL")
        app.state.generate = None
        if key and model:
            with genai.Client(
                api_key=key, http_options=types.HttpOptions(timeout=20000)
            ) as client:
                app.state.generate = partial(
                    client.models.generate_content, model=model
                )
                yield
        else:
            yield


router = APIRouter(lifespan=lifespan)


@router.post("/v1/chat")
def post_chat(body: ChatRequest, request: Request):
    if body.messages[-1].role != "user":
        raise HTTPException(422, "The last message must be from the user.")
    state = request.app.state
    if state.generate is None:
        raise HTTPException(503, "Set GEMINI_API_KEY and GEMINI_MODEL to enable chat.")
    host = request.client.host if request.client else "unknown"
    with state.chat_lock:
        count = state.chat_counts.get(host, 0)
        if count >= 10:
            raise HTTPException(
                429, "Chat request limit reached.", headers={"Retry-After": "60"}
            )
        state.chat_counts[host] = count + 1
    try:
        return chat(body, state.generate, state.geocoder, state.parking)
    except ServiceUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    except (httpx.HTTPError, errors.APIError):
        raise HTTPException(
            503, "A parking chat service is temporarily unavailable."
        ) from None
