"""Local page server. Serves only synthetic content; no outbound traffic.

GET /page/{size}/{n}   synthetic article (size: 100k,1m,5m,20m,50m). Pages <= 1m differ per n.
GET /file/{name}?mb=100  streamed application/zip body (not held in memory).
"""

import zlib
from fastapi import FastAPI, Response
from fastapi.responses import StreamingResponse
from gen import gen

app = FastAPI()
SIZES = {
    "100k": 100_000,
    "1m": 1_000_000,
    "5m": 5_000_000,
    "20m": 20_000_000,
    "50m": 50_000_000,
}
_cache: dict = {}


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/page/{size}/{n}")
def page(size: str, n: str):
    key = (size, zlib.crc32(n.encode()) % 1000 if SIZES[size] <= 1_000_000 else 0)
    if key not in _cache:
        if len(_cache) > 50:
            _cache.clear()
        _cache[key] = gen(SIZES[size], seed=5 + key[1])
    return Response(content=_cache[key], media_type="text/html")


@app.get("/file/{name}")
def file(name: str, mb: int = 100):
    block = bytes((i * 31 + 7) % 256 for i in range(1 << 20))
    total = mb

    def body():
        for _ in range(total):
            yield block

    return StreamingResponse(
        body(),
        media_type="application/zip",
        headers={"Content-Length": str(total << 20)},
    )


@app.get("/img/{x}")
def img(x: str):
    return Response(content=b"", media_type="image/jpeg")
