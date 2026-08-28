"""CookAlong - FastAPI backend.

Stateless by design. The ONLY server state is an in-memory dict of parsed
recipes keyed by video_id, so two people demoing the same video don't spend two
sets of API calls. Losing it on restart is harmless. All user data (pantry,
library, progress) lives in the browser's localStorage - there is deliberately
no /api/inventory and no /api/check.
"""
import asyncio
import logging
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import llm
import serpapi_client as serp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("cookalong")

REQUIRED_KEYS = ["ANTHROPIC_API_KEY", "SERPAPI_KEY"]
OPTIONAL_KEYS = ["TAVILY_API_KEY"]
SHOP_ITEM_CAP = 8

app = FastAPI(title="CookAlong")

# video_id -> parsed recipe. Free, and it protects a 250-search/month SerpApi tier.
_recipe_cache: dict[str, dict] = {}


def _missing_required():
    return [k for k in REQUIRED_KEYS if not os.environ.get(k)]


@app.on_event("startup")
async def check_keys():
    """Fail loudly, naming the variable - but with a readable 503 rather than a
    crash-loop, which on Cloud Run surfaces only as 'container failed to start'."""
    missing = _missing_required()
    for key in missing:
        log.critical("MISSING REQUIRED ENVIRONMENT VARIABLE: %s", key)
    for key in OPTIONAL_KEYS:
        if not os.environ.get(key):
            log.warning("%s not set - shopping links fall back to a Walmart search", key)
    if not missing:
        log.info("all required keys present")


class RecipeRequest(BaseModel):
    url: str = ""
    demo: bool = False


class ShopRequest(BaseModel):
    items: list[str] = []


@app.post("/api/recipe")
async def api_recipe(req: RecipeRequest):
    if req.demo:
        video = serp.load_sample()
    else:
        missing = _missing_required()
        if missing:
            raise HTTPException(503, f"Server is missing {', '.join(missing)}.")
        try:
            video_id = serp.extract_video_id(req.url)
        except serp.SerpApiError as exc:
            raise HTTPException(400, str(exc))

        if video_id in _recipe_cache:
            log.info("cache hit for %s", video_id)
            return _recipe_cache[video_id]

        try:
            video = await serp.fetch_video(video_id)
        except serp.SerpApiError as exc:
            raise HTTPException(502, str(exc))

    log.info(
        "extracting %s: %s lines, %ss, %s chapters",
        video["video_id"], len(video["lines"]), video["duration_seconds"],
        len(video["chapters"]),
    )
    try:
        recipe = await llm.extract_recipe(video)
    except Exception as exc:
        log.exception("extraction failed")
        raise HTTPException(502, f"Could not read the recipe: {exc}")

    _recipe_cache[recipe["video_id"]] = recipe
    return recipe


async def _walmart_fallback(item):
    query = item.replace(" ", "+")
    return [{
        "title": f"Search Walmart for {item}",
        "url": f"https://www.walmart.com/search?q={query}",
        "source": "walmart.com",
    }]


async def _tavily(client, item, key):
    try:
        response = await client.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "query": f"buy {item} online grocery delivery walmart instacart",
                "max_results": 3,
            },
        )
        response.raise_for_status()
        results = []
        for hit in (response.json().get("results") or [])[:3]:
            url = hit.get("url") or ""
            results.append({
                "title": hit.get("title") or item,
                "url": url,
                "source": url.split("/")[2] if "//" in url else "",
            })
        return results or await _walmart_fallback(item)
    except Exception as exc:
        log.warning("tavily failed for %r, falling back: %s", item, exc)
        return await _walmart_fallback(item)


@app.post("/api/shop")
async def api_shop(req: ShopRequest):
    items = [i.strip() for i in req.items if i and i.strip()][:SHOP_ITEM_CAP]
    if not items:
        return {"results": {}}

    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        return {"results": {i: await _walmart_fallback(i) for i in items}}

    async with httpx.AsyncClient(timeout=20.0) as client:
        found = await asyncio.gather(*(_tavily(client, i, key) for i in items))
    return {"results": dict(zip(items, found))}


@app.get("/api/health")
async def health():
    return {"ok": True, "missing_keys": _missing_required(),
            "cached_recipes": len(_recipe_cache)}


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


# Mounted last so it never shadows /api/*.
app.mount("/", StaticFiles(directory=str(Path(__file__).parent / "static"), html=True))
