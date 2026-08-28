"""Recipe extraction + the single repair call.

OpenAI is the primary provider; Gemini is the fallback. Both are driven with the
same JSON schema (OpenAI strict `json_schema` response_format, Gemini
`response_json_schema`), so the output shape is enforced by the provider rather
than by asking the prompt for raw JSON. The only remaining way to get bad JSON is
truncation, which is why finish_reason is checked.

If OpenAI fails for any reason - no key, quota, rate limit, an outage - the call
falls through to Gemini automatically and logs which provider served it.
"""
import json
import logging
import os

import prompts
import validate

log = logging.getLogger("cookalong.llm")

OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

EXTRACTION_MAX_TOKENS = 16000
REPAIR_MAX_TOKENS = 8000

_openai_client = None
_gemini_client = None


def _openai():
    global _openai_client
    if _openai_client is None:
        from openai import AsyncOpenAI
        _openai_client = AsyncOpenAI()          # reads OPENAI_API_KEY
    return _openai_client


def _gemini():
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        _gemini_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    return _gemini_client


def has_openai():
    return bool(os.environ.get("OPENAI_API_KEY"))


def has_gemini():
    return bool(os.environ.get("GEMINI_API_KEY"))


def _parse_json(text):
    """The schema makes this valid JSON; the fence-strip is belt-and-braces."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return json.loads(text)


async def _via_openai(system, user, schema, max_tokens):
    response = await _openai().chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_completion_tokens=max_tokens,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "recipe", "strict": True, "schema": schema},
        },
    )
    choice = response.choices[0]
    if choice.finish_reason == "length":
        log.error("TRUNCATED at max_completion_tokens=%s - JSON will not parse", max_tokens)
    usage = response.usage
    log.info(
        "openai %s in=%s out=%s finish=%s",
        OPENAI_MODEL,
        getattr(usage, "prompt_tokens", "?"),
        getattr(usage, "completion_tokens", "?"),
        choice.finish_reason,
    )
    return _parse_json(choice.message.content)


async def _via_gemini(system, user, schema, max_tokens):
    from google.genai import types

    response = await _gemini().aio.models.generate_content(
        model=GEMINI_MODEL,
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=schema,
            max_output_tokens=max_tokens,
        ),
    )
    log.info("gemini %s served the request", GEMINI_MODEL)
    return _parse_json(response.text)


async def _complete(system, user, schema, max_tokens):
    """OpenAI first, Gemini on any failure. Raises only if both are unusable."""
    errors = []

    if has_openai():
        try:
            return await _via_openai(system, user, schema, max_tokens)
        except Exception as exc:
            errors.append(f"OpenAI: {exc}")
            log.warning("openai failed, falling back to gemini: %s", exc)
    else:
        errors.append("OpenAI: OPENAI_API_KEY not set")

    if has_gemini():
        try:
            return await _via_gemini(system, user, schema, max_tokens)
        except Exception as exc:
            errors.append(f"Gemini: {exc}")
            log.error("gemini also failed: %s", exc)
    else:
        errors.append("Gemini: GEMINI_API_KEY not set")

    raise RuntimeError(" | ".join(errors))


async def extract_recipe(video):
    """Transcript -> structured recipe, with at most one repair call."""
    data = await _complete(
        prompts.EXTRACTION_SYSTEM,
        prompts.build_extraction_prompt(video),
        prompts.RECIPE_SCHEMA,
        EXTRACTION_MAX_TOKENS,
    )

    duration = video["duration_seconds"]
    steps = validate.finalize_steps(data.get("steps"), duration)
    report, window = validate.coverage(steps, duration)

    if window:
        try:
            repaired = await _complete(
                prompts.REPAIR_SYSTEM,
                prompts.build_repair_prompt(
                    steps, video["lines"], window["start"], window["end"]
                ),
                prompts.REPAIR_SCHEMA,
                REPAIR_MAX_TOKENS,
            )
            steps = validate.merge_steps(steps, repaired.get("steps"), duration)
            report, _ = validate.coverage(steps, duration)
            log.info("repair merged; now %s steps", len(steps))
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
