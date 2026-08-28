"""Extraction + repair prompts and the JSON schema the model is held to.

The schema is enforced by the API via output_config.format, so malformed JSON
is impossible at the protocol level. `index` and `end_seconds` are deliberately
NOT in the schema - the server derives them from step order, which is more
reliable than asking the model to count.
"""

COURSE_TAGS = [
    "Breakfast", "Appetizer", "Salad", "Soup", "Main",
    "Side", "Dessert", "Snack", "Drink", "Sauce",
]
CUISINE_TAGS = [
    "Italian", "Mexican", "Chinese", "Japanese", "Indian", "Thai",
    "Mediterranean", "American", "French", "Korean", "Middle Eastern", "Other",
]
ATTRIBUTE_TAGS = [
    "Vegetarian", "Vegan", "Gluten-Free", "Quick",
    "One-Pot", "Baked", "Grilled", "No-Cook",
]
ALL_TAGS = COURSE_TAGS + CUISINE_TAGS + ATTRIBUTE_TAGS

CATEGORIES = ["Produce", "Protein", "Dairy", "Pantry", "Spices", "Other"]

_DETAIL_SCHEMA = {
    "type": "object",
    "properties": {
        "how": {"type": "string"},
        "watch_for": {"type": "string"},
        "duration_hint": {"type": "string"},
    },
    "required": ["how", "watch_for", "duration_hint"],
    "additionalProperties": False,
}

_STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "start_seconds": {"type": "integer"},
        "narration": {"type": "string"},
        "detail": {"anyOf": [_DETAIL_SCHEMA, {"type": "null"}]},
        "terms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "term": {"type": "string"},
                    "start_seconds": {"type": "integer"},
                },
                "required": ["term", "start_seconds"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["text", "start_seconds", "narration", "detail", "terms"],
    "additionalProperties": False,
}

RECIPE_SCHEMA = {
    "type": "object",
    "properties": {
        "tags": {"type": "array", "items": {"type": "string", "enum": ALL_TAGS}},
        "servings": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        "total_time_minutes": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        "ingredients": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "quantity": {"type": "string"},
                    "unit": {"type": "string"},
                    "normalized": {"type": "string"},
                    "category": {"type": "string", "enum": CATEGORIES},
                },
                "required": ["name", "quantity", "unit", "normalized", "category"],
                "additionalProperties": False,
            },
        },
        "steps": {"type": "array", "items": _STEP_SCHEMA},
    },
    "required": ["tags", "servings", "total_time_minutes", "ingredients", "steps"],
    "additionalProperties": False,
}

REPAIR_SCHEMA = {
    "type": "object",
    "properties": {"steps": {"type": "array", "items": _STEP_SCHEMA}},
    "required": ["steps"],
    "additionalProperties": False,
}

_COURSE_LIST = ", ".join(COURSE_TAGS)
_CUISINE_LIST = ", ".join(CUISINE_TAGS)
_ATTRIBUTE_LIST = ", ".join(ATTRIBUTE_TAGS)
_CATEGORY_LIST = ", ".join(CATEGORIES)

EXTRACTION_SYSTEM = f"""You turn a YouTube cooking video transcript into a complete, structured recipe that someone can cook from without watching the video.

COMPLETENESS IS THE REQUIREMENT.

- Cover the recipe end to end: prep, cooking, finishing, plating, garnish, serving.
  Auto-generated transcripts trail off into outros and sponsor reads - ignore those,
  but do not stop early because of them.
- Produce 8-20 steps. If the video is long or involved, use more steps rather than
  cramming several actions into one.
- Walk the transcript in order and account for every cooking action mentioned. A
  viewer following only your steps must be able to finish the dish.
- start_seconds must be an ACTUAL timestamp that appears in the transcript, in the
  [seconds] markers. Never invent one.
- Steps must be in strictly increasing start_seconds order.
- When a chapter outline is provided, align your step boundaries to it.
- narration is a clean, spoken-aloud rewrite of the step - the transcript is
  lowercase, unpunctuated and full of filler; your narration is not.

DETAIL:

- Fill detail for any step involving a technique, a judgment call ("until golden"),
  or something a beginner could get wrong.
- how: 2-4 sentences of concrete mechanics. Assume the reader has never done it.
- watch_for: the common mistake or the failure signal.
- duration_hint: rough time, only where meaningful.
- Use null for trivial steps ("season with salt"). Padding every step with detail
  makes the interface noise. Do not pad.

TERMS:

- terms lists cooking technique words that appear inside that step's text
  (for example "julienne", "deglaze", "fold"), each with the transcript timestamp
  where the technique is demonstrated. Empty array if the step has no technique term.

TAGS - pick only from these fixed vocabularies:

- Course (at least one, required): {_COURSE_LIST}
- Cuisine (at most one): {_CUISINE_LIST}
- Attributes (any number): {_ATTRIBUTE_LIST}

Ingredient category must be one of: {_CATEGORY_LIST}.
Use an empty string for quantity or unit when the video never states one."""

REPAIR_SYSTEM = """You are repairing an incomplete recipe extraction.

You will be given the steps already extracted and the portion of the transcript that
those steps FAIL to cover. Return ONLY the additional steps needed for that uncovered
window, in the same schema. Do not repeat steps that already exist. Every
start_seconds must be an actual timestamp from the transcript excerpt shown."""


def format_transcript(lines):
    """`[142] text here` - seconds, never milliseconds. Never truncated."""
    return "\n".join(f"[{line['seconds']}] {line['text']}" for line in lines)


def build_extraction_prompt(video):
    parts = [
        f"Video title: {video['title']}",
        f"Channel: {video['channel']['name']}",
        f"Video duration: {video['duration_seconds']} seconds",
    ]
    if video.get("chapters"):
        outline = "\n".join(
            f"  [{c['start_seconds']}] {c['title']}" for c in video["chapters"]
        )
        parts.append(f"Chapter outline from the creator:\n{outline}")
    parts.append(
        "Full transcript, one line per entry as [seconds] text:\n\n"
        + format_transcript(video["lines"])
    )
    return "\n\n".join(parts)


def build_repair_prompt(steps, lines, window_start, window_end):
    existing = "\n".join(f"  [{s['start_seconds']}] {s['text']}" for s in steps)
    excerpt = format_transcript(
        [l for l in lines if window_start <= l["seconds"] <= window_end]
    )
    return (
        f"Steps already extracted:\n{existing}\n\n"
        f"These steps do not cover the window from {window_start}s to {window_end}s. "
        f"Here is the transcript for that window:\n\n{excerpt}\n\n"
        f"Return the missing steps for this window only."
    )
