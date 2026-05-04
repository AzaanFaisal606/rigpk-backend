import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.routers.parts import router
from backend.routers.builds import router as builds_router
from backend.routers.prebuilts import router as prebuilts_router

app = FastAPI(title="RigPK API")

_origins = [
    "http://localhost:3000",
    "http://localhost:3001",
]
if _frontend_url := os.getenv("FRONTEND_URL"):
    _origins.append(_frontend_url)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(builds_router)
app.include_router(prebuilts_router)
