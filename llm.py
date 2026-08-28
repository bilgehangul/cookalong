"""Anthropic extraction + the single repair call.

Model is claude-sonnet-5, so `temperature` is NOT sent - it was removed from the
API and returns a 400. Output shape is enforced by output_config.format rather
than by asking the prompt for raw JSON, which makes malformed JSON impossible at
the protocol level; the only remaining way to get bad JSON is truncation, which
is why stop_reason is checked.
"""
import json
import logging
import os

import anthropic

import prompts
import validate

log = logging.getLogger("cookalong.llm")

MODEL = "claude-sonnet-5"
EXTRACTION_MAX_TOKENS = 16000
REPAIR_MAX_TOKENS = 8000

_client = None


def client():
    global _client
    if _client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY is not set on the server.")
        _client = anthropic.AsyncAnthropic()
    return _client


def _parse_json(text):
    """output_config.format guarantees valid JSON; this is belt-and-braces."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return json.loads(text)


async def _call(system, user, schema, max_tokens, effort):
    """Streamed so a long structured response can't hit an HTTP timeout."""
    async with client().messages.stream(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        thinking={"type": "adaptive"},
        output_config={
            "effort": effort,
            "format": {"type": "json_schema", "schema": schema},
        },
    ) as stream:
        message = await stream.get_final_message()

    if message.stop_reason == "refusal":
        raise RuntimeError("The model declined to process this video.")
    if message.stop_reason == "max_tokens":
        log.error("TRUNCATED at max_tokens=%s - response JSON will not parse", max_tokens)

    text = next((b.text for b in message.content if b.type == "text"), "")
    log.info(
        "usage in=%s out=%s stop=%s",
        message.usage.input_tokens, message.usage.output_tokens, message.stop_reason,
    )
    return _parse_json(text)


async def extract_recipe(video):
    """Transcript -> structured recipe, with at most one repair call."""
    data = await _call(
        prompts.EXTRACTION_SYSTEM,
        prompts.build_extraction_prompt(video),
        prompts.RECIPE_SCHEMA,
        EXTRACTION_MAX_TOKENS,
        "high",
    )

    duration = video["duration_seconds"]
    steps = validate.finalize_steps(data.get("steps"), duration)
    report, window = validate.coverage(steps, duration)

    if window:
        try:
            repaired = await _call(
                prompts.REPAIR_SYSTEM,
                prompts.build_repair_prompt(
                    steps, video["lines"], window["start"], window["end"]
                ),
                prompts.REPAIR_SCHEMA,
                REPAIR_MAX_TOKENS,
                "medium",
            )
            steps = validate.merge_steps(steps, repaired.get("steps"), duration)
            report, _ = validate.coverage(steps, duration)
            log.info("repair added steps; now %s steps", len(steps))
        except Exception as exc:
            # One retry maximum. A slightly gappy recipe beats a spinner.
            log.warning("repair call failed, returning what we have: %s", exc)

    return {
        "video_id": video["video_id"],
        "title": video["title"],
        "channel": video["channel"],
        "thumbnail": video["thumbnail"],
        "duration_seconds": duration,
        "tags": validate.normalize_tags(data.get("tags")),
        "servings": data.get("servings"),
        "total_time_minutes": data.get("total_time_minutes"),
        "ingredients": data.get("ingredients") or [],
        "steps": steps,
        "coverage": report,
    }
