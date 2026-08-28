"""SerpApi: video metadata + timestamped transcript, fetched concurrently.

Milliseconds are converted to seconds once, here, at parse time. No ms value
ever leaves this module.
"""
import asyncio
import json
import logging
import os
import re
from pathlib import Path

import httpx

log = logging.getLogger("cookalong.serpapi")

SERPAPI_URL = "https://serpapi.com/search"
TIMEOUT = 30.0
SAMPLE_PATH = Path(__file__).parent / "sample_video.json"

# youtube.com/watch?v=ID, youtu.be/ID, /shorts/ID, /embed/ID, /live/ID
_VIDEO_ID_PATTERNS = [
    re.compile(r"[?&]v=([A-Za-z0-9_-]{11})"),
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})"),
    re.compile(r"/(?:shorts|embed|live|v)/([A-Za-z0-9_-]{11})"),
]


class SerpApiError(Exception):
    """Surfaced to the client as a clean message."""


def extract_video_id(url: str) -> str:
    url = (url or "").strip()
    for pattern in _VIDEO_ID_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)
    # A bare video ID pasted on its own.
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url):
        return url
    raise SerpApiError("Could not find a YouTube video ID in that URL.")


def _api_key() -> str:
    key = os.environ.get("SERPAPI_KEY")
    if not key:
        raise SerpApiError("SERPAPI_KEY is not set on the server.")
    return key


async def _get(client: httpx.AsyncClient, params: dict) -> dict:
    """One SerpApi call. `no_cache` is deliberately unset - cached hits are free."""
    response = await client.get(SERPAPI_URL, params={**params, "api_key": _api_key()})
    response.raise_for_status()
    data = response.json()
    status = (data.get("search_metadata") or {}).get("status")
    if status == "Error":
        raise SerpApiError(data.get("error") or "SerpApi returned an error.")
    return data


async def _fetch_metadata(client: httpx.AsyncClient, video_id: str) -> dict:
    return await _get(client, {"engine": "youtube_video", "v": video_id})


async def _fetch_transcript(client: httpx.AsyncClient, video_id: str) -> dict:
    """ASR captions by default; one retry against whatever the video actually has."""
    data = await _get(
        client,
        {"engine": "youtube_video_transcript", "v": video_id, "type": "asr"},
    )
    if data.get("transcript"):
        return data

    available = data.get("available_transcripts") or []
    if not available:
        return data

    alt = available[0]
    log.info(
        "empty asr transcript, retrying with type=%s language_code=%s",
        alt.get("type"), alt.get("language_code"),
    )
    params = {"engine": "youtube_video_transcript", "v": video_id}
    if alt.get("type"):
        params["type"] = alt["type"]
    if alt.get("language_code"):
        params["language_code"] = alt["language_code"]
    return await _get(client, params)


def parse_metadata(raw: dict | None) -> dict:
    """Degrade gracefully - a metadata failure must never block the transcript."""
    raw = raw or {}
    channel = raw.get("channel") or {}
    return {
        "title": raw.get("title") or "Recipe",
        "thumbnail": raw.get("thumbnail") or "",
        "channel": {
            "name": channel.get("name") or "Unknown",
            "link": channel.get("link") or "",
        },
    }


def parse_transcript(raw: dict) -> dict:
    """-> {lines: [{seconds, text}], chapters: [...], duration_seconds: int}"""
    entries = raw.get("transcript") or []
    if not entries:
        raise SerpApiError(
            "No transcript available - try a video with captions enabled."
        )

    lines = []
    for entry in entries:
        text = (entry.get("snippet") or "").strip()
        if not text:
            continue
        lines.append({"seconds": int(entry.get("start_ms") or 0) // 1000, "text": text})

    # Duration comes from the last entry's end_ms, and it drives every coverage check.
    last_end_ms = max((int(e.get("end_ms") or 0) for e in entries), default=0)
    duration_seconds = last_end_ms // 1000

    chapters = []
    for chapter in raw.get("chapters") or []:
        chapters.append({
            "title": chapter.get("chapter") or "",
            "start_seconds": int(chapter.get("start_ms") or 0) // 1000,
        })

    return {
        "lines": lines,
        "chapters": chapters,
        "duration_seconds": duration_seconds,
    }


async def fetch_video(video_id: str) -> dict:
    """Both engines concurrently. Metadata failure degrades; transcript failure raises."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        meta_raw, transcript_raw = await asyncio.gather(
            _fetch_metadata(client, video_id),
            _fetch_transcript(client, video_id),
            return_exceptions=True,
        )

    if isinstance(transcript_raw, BaseException):
        if isinstance(transcript_raw, SerpApiError):
            raise transcript_raw
        raise SerpApiError(f"Transcript fetch failed: {transcript_raw}")

    if isinstance(meta_raw, BaseException):
        log.warning("metadata fetch failed, degrading: %s", meta_raw)
        meta_raw = None

    _save_sample(meta_raw, transcript_raw)
    return _assemble(meta_raw, transcript_raw, video_id)


def _assemble(meta_raw, transcript_raw, video_id: str) -> dict:
    parsed = parse_transcript(transcript_raw)
    return {
        "video_id": video_id,
        **parse_metadata(meta_raw),
        **parsed,
    }


def _save_sample(meta_raw, transcript_raw) -> None:
    """Demo safety: cache raw responses so ?demo=1 works with the wifi off."""
    if SAMPLE_PATH.exists():
        return
    try:
        SAMPLE_PATH.write_text(
            json.dumps({"metadata": meta_raw, "transcript": transcript_raw}, indent=2),
            encoding="utf-8",
        )
        log.info("wrote demo fallback to %s", SAMPLE_PATH.name)
    except OSError as exc:
        log.warning("could not write sample_video.json: %s", exc)


def load_sample() -> dict:
    """?demo=1 - replay the cached responses instead of calling out."""
    if not SAMPLE_PATH.exists():
        raise SerpApiError(
            "Demo mode needs sample_video.json - load one real video first."
        )
    raw = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    meta_raw = raw.get("metadata")
    video_id = (meta_raw or {}).get("video_id") or raw.get("video_id") or "demo"
    return _assemble(meta_raw, raw.get("transcript") or {}, video_id)
