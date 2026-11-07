"""FastAPI Chat Route with Gemini Function Calling and Rate Limiting."""

import time
from collections import defaultdict
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.parking.llm.gemini_client import run_chat_turn

router = APIRouter()

# Simple sliding window rate limiter (max 30 requests per minute per client IP)
_CLIENT_REQUESTS = defaultdict(list)
RATE_LIMIT_MAX = 30
RATE_LIMIT_WINDOW = 60.0  # seconds


class MessageItem(BaseModel):
    role: str = Field(..., description="'user' or 'assistant'")
    text: str = Field(..., description="Message text")


class UserLocation(BaseModel):
    lat: float
    lon: float


class ChatRequest(BaseModel):
    messages: List[MessageItem] = Field(..., description="Recent conversation turns (capped at ~10)")
    user_location: Optional[UserLocation] = None


class ChatResponse(BaseModel):
    reply: str
    results: List[Dict[str, Any]] = []
    data_as_of: Optional[str] = None
    query: Optional[Dict[str, Any]] = None


@router.post("/v1/chat", response_model=ChatResponse)
def post_chat(req: ChatRequest, request: Request) -> ChatResponse:
    """Conversational parking assistant powered by Google Gemini and live tool execution."""
    client_ip = request.client.host if request.client else "127.0.0.1"
    now = time.time()

    # Rate limit check
    req_times = _CLIENT_REQUESTS[client_ip]
    _CLIENT_REQUESTS[client_ip] = [t for t in req_times if now - t < RATE_LIMIT_WINDOW]
    if len(_CLIENT_REQUESTS[client_ip]) >= RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Please wait a moment before sending another message."
        )
    _CLIENT_REQUESTS[client_ip].append(now)

    # Cap message history to last 10 turns
    history = [m.model_dump() for m in req.messages[-10:]]
    user_loc = req.user_location.model_dump() if req.user_location else None

    turn_res = run_chat_turn(messages=history, user_location=user_loc)

    return ChatResponse(
        reply=turn_res.get("reply", ""),
        results=turn_res.get("results", []),
        data_as_of=turn_res.get("data_as_of"),
        query=turn_res.get("query"),
    )

