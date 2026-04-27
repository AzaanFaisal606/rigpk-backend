import os
import sys
from pathlib import Path

# Allow running as `uvicorn backend.main:app` from project root
# or `uvicorn main:app` from within backend/
_backend_dir = Path(__file__).parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers.parts import router
from routers.builds import router as builds_router
from routers.prebuilts import router as prebuilts_router

app = FastAPI(title="PakPC API")

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
