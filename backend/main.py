import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from backend.deps import lifespan
from backend.routers.parts import router
from backend.routers.builds import router as builds_router
from backend.routers.prebuilts import router as prebuilts_router
from backend.routers.trends import router as trends_router

app = FastAPI(title="RigPK API", lifespan=lifespan)

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
app.add_middleware(GZipMiddleware, minimum_size=1000)

app.include_router(router)
app.include_router(builds_router)
app.include_router(prebuilts_router)
app.include_router(trends_router)

# Weekly-changing data served with no cache headers means every visitor
# re-fetches the whole catalogue and the CDN in front of Render cannot help.
# Routes that set their own header (search-index has an ETag) keep theirs.
_CACHE_RULES = (
    ("/api/parts/filters", 3600),
    ("/api/trends",        3600),
    ("/api/search-index",  None),   # sets its own
    ("/api/parts",          900),
    ("/api/prebuilts",      900),
    ("/api/stats",          300),
)


@app.middleware("http")
async def cache_control(request, call_next):
    response = await call_next(request)
    if response.status_code >= 300:
        response.headers.setdefault("Cache-Control", "no-store")
        return response
    if request.method != "GET":
        response.headers.setdefault("Cache-Control", "no-store")
        return response
    if "Cache-Control" in response.headers:
        return response
    # /api/builds/share/{code} resolves to one user's build — a shared
    # cache (CDN/proxy) must never store it under a public key.
    if request.url.path.startswith("/api/builds"):
        response.headers["Cache-Control"] = "private, no-store"
        return response
    for prefix, max_age in _CACHE_RULES:
        if request.url.path.startswith(prefix):
            if max_age:
                response.headers["Cache-Control"] = f"public, max-age={max_age}"
            return response
    return response
