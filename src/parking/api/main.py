import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from parking.api.routes_chat import router
from parking.api.routes_spots import router as spots_router

load_dotenv()
app = FastAPI()
app.include_router(spots_router)
app.include_router(router)
if origin := os.getenv("FRONTEND_ORIGIN"):
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
