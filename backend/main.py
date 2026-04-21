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

app = FastAPI(title="PakPC API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
