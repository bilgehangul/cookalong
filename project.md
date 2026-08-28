# PROJECT.md — CookAlong (Hackathon Build)

## Context for the agent

This is a **1–2 hour hackathon build**. Optimize for a working demo, not for
architecture. Prefer boring, single-file solutions. Do not add tests, auth,
databases, migrations, or CI. Ship it, then stop.

You are responsible for the full loop: scaffolding, writing the code, running
it locally, fixing errors, deploying to Google Cloud Run, and reporting the
public URL back to the user.

---

## What we're building

A web app that turns a YouTube recipe video into a **cook-along interface**:

1. User pastes a YouTube recipe URL.
2. We pull **video metadata** (title, channel, thumbnail) and the **timestamped
   transcript** from SerpApi, in parallel.
3. An LLM converts the transcript into a complete structured recipe:
   ingredients, **every step start to finish**, expandable how-to detail under
   each step, and category tags.
4. Before cooking, an **ingredient check** against the user's saved pantry.
   Missing items get shopping links.
5. Cook mode: video pinned at top, steps below. **Clicking any step seeks the
   video to that timestamp.** Technique terms are individually clickable.
6. Every recipe is saved to a **library**, filterable by tag (Dessert, Salad,
   Appetizer…) and by **YouTube channel**.

All user data lives in the browser. No accounts, no server-side state.

---

## Stack (do not deviate — chosen for speed)

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.11 + FastAPI + Uvicorn | **Fully stateless** |
| Frontend | Single `index.html`, vanilla JS, Tailwind via CDN | Zero build step |
| Video metadata | SerpApi `youtube_video` engine | Title, channel, thumbnail |
| Transcript | SerpApi `youtube_video_transcript` engine | Timestamps + chapters |
| LLM | Anthropic API, `claude-sonnet-4-6` | Structured extraction |
| Shopping | Tavily Search API | Retailer links |
| **Persistence** | **`localStorage` in the browser** | No accounts, no shared state |
| Hosting | Google Cloud Run (source deploy) | One command |

---

## Persistence: browser-only, and why this is not optional

**Do not write user data to disk on the server.** Cloud Run containers have an
ephemeral filesystem and multiple instances. A server-side `inventory.json`
would mean every visitor shares one pantry, and the whole thing resets on cold
start or redeploy. That's a broken demo, not a shortcut.

Everything user-specific lives in `localStorage`:

| Key | Contents |
|---|---|
| `cookalong:pantry:v1` | `[{ name, have, category }]` |
| `cookalong:recipes:v1` | Array of full recipe objects, keyed by `video_id` |
| `cookalong:progress:v1` | `{ [video_id]: [checked step indices] }` |

Rules:
- Wrap every read in `try/catch` and fall back to defaults. A malformed or
  full `localStorage` must never white-screen the app.
- Version the keys (`:v1`). If you change a shape mid-hack, bump to `:v2`
  rather than writing migration code.
- Seed the pantry on first visit only — gate on the key's absence, not on an
  empty array, or "I have nothing" gets overwritten every reload.
- The library caches full recipe JSON, so re-opening a saved recipe costs zero
  API calls. This matters on a 250-search/month SerpApi tier.
- Add a small "Reset all data" button in the pantry footer. Judges will click
  it; better it exists than they wonder.

**The server keeps only one thing:** an in-memory `dict` cache of parsed
recipes keyed by `video_id`, so two people demoing the same video don't spend
two sets of API calls. Losing it on restart is harmless.

Consequence: there is **no `/api/inventory` or `/api/check`**. Pantry state and
have/need matching are pure client-side JS. Less code, and it's correct.

---

## SerpApi: two calls, run concurrently

Extract the video ID with a regex handling both `youtube.com/watch?v=ID` and
`youtu.be/ID`. Then fire both requests together with `asyncio.gather` — the
transcript alone doesn't carry title or channel.

### Call 1 — metadata (`engine=youtube_video`)

```
GET https://serpapi.com/search
  ?engine=youtube_video&v=VIDEO_ID&api_key=SERPAPI_KEY
```

Returns `title`, `thumbnail`, `views`, and a `channel` object:

```json
{
  "title": "The basics about: Coffee",
  "thumbnail": "https://i.ytimg.com/vi/.../maxresdefault.jpg",
  "channel": {
    "name": "Old Game Box",
    "link": "https://www.youtube.com/@OldGameBox",
    "thumbnail": "https://yt3.ggpht.com/...",
    "subscribers": "15.4K subscribers"
  },
  "views": "2,138,310 views"
}
```

Take `title`, `thumbnail`, `channel.name`, `channel.link`. **`channel.name` is
the searchable metadata field** for the library. If this call fails, degrade
gracefully: title falls back to `"Recipe"`, channel to `"Unknown"`. Never let a
metadata failure block the transcript.

### Call 2 — transcript (`engine=youtube_video_transcript`)

```
GET https://serpapi.com/search
  ?engine=youtube_video_transcript&v=VIDEO_ID&type=asr&api_key=SERPAPI_KEY
```

- `v` — **required**, the video ID
- `type=asr` — auto-generated captions. Most recipe channels have no
  hand-written ones, so send `asr` by default.
- `language_code` — optional, defaults to `en`; falls back to the first
  available language if the requested one is missing.

Response:

```json
{
  "transcript": [
    { "start_ms": 240, "end_ms": 7040, "snippet": "hello everyone and welcome to", "start_time_text": "0:00" }
  ],
  "chapters": [
    { "chapter": "Prepping the vegetables", "start_ms": 214000, "end_ms": 289000 }
  ],
  "available_transcripts": [
    { "language_name": "English", "language_code": "en", "type": "asr", "selected": true }
  ]
}
```

**Implementation notes:**

- Times are **milliseconds**; `player.seekTo()` takes **seconds**. Convert once
  at parse time (`start_ms // 1000`) and never let ms reach the frontend.
- **`chapters` is a gift.** Creator-authored section boundaries, often exactly
  "Prep / Sauce / Cook / Plate" on recipe videos. Pass them to the LLM as
  structural hints. Handle absence — many videos have none.
- Derive `video_duration_seconds` from the **last transcript entry's `end_ms`**.
  You need this number for the coverage checks below.
- If `transcript` is empty, inspect `available_transcripts` and retry once with
  another `type` or `language_code`. If still empty, return a clean error:
  *"No transcript available — try a video with captions enabled."*
- Check `search_metadata.status` (`Processing` → `Success` | `Error`); on error
  surface the `error` field.
- **Caching is free.** SerpApi serves a cached result for 1h when query and
  parameters match exactly, and cached searches don't count against quota.
  Leave `no_cache` unset. Rehearsing on the same video costs nothing.
- Free tier is 250 searches/month and each recipe now costs **2**. The
  localStorage library and the server-side dict cache both exist to protect
  this.
- 30s timeout per call; typical response ~1s.

**Demo safety:** after the first successful fetch, dump both raw responses to
`sample_video.json`. A `?demo=1` flag loads them instead of calling out. Build
this in phase 1 — venue wifi fails at the worst possible moment.

---

## API contract

### `POST /api/recipe`

```json
// request
{ "url": "https://www.youtube.com/watch?v=VIDEO_ID" }
```

```json
// response
{
  "video_id": "VIDEO_ID",
  "title": "Restaurant-Style Fried Rice",
  "channel": { "name": "Chef Wang", "link": "https://youtube.com/@chefwang" },
  "thumbnail": "https://i.ytimg.com/vi/VIDEO_ID/maxresdefault.jpg",
  "duration_seconds": 812,
  "tags": ["Main", "Chinese", "Quick", "One-pot"],
  "servings": 2,
  "total_time_minutes": 25,
  "ingredients": [
    { "name": "carrot", "quantity": "2", "unit": "medium",
      "normalized": "carrot", "category": "Produce" }
  ],
  "steps": [
    {
      "index": 1,
      "text": "Julienne the carrots into thin matchsticks.",
      "start_seconds": 142,
      "end_seconds": 178,
      "narration": "Slice the carrots into thin, even matchsticks about the width of a toothpick.",
      "detail": {
        "how": "Cut the carrot into 2-inch lengths. Slice a thin strip off one side so it lies flat and won't roll. Slice into thin planks, stack a few planks, then cut the stack into matchsticks.",
        "watch_for": "Skipping the flat side is how people cut themselves — the carrot rolls under the knife.",
        "duration_hint": "About 3 minutes for two carrots."
      },
      "terms": [ { "term": "julienne", "start_seconds": 145 } ]
    }
  ],
  "coverage": { "first_step_at": 142, "last_step_at": 764, "steps": 11 }
}
```

`detail` may be `null` for steps that need no elaboration ("season with salt").
Don't pad it.

### `POST /api/shop`

Body: `{ "items": ["carrot", "sesame oil"] }`. For each, query Tavily with
`buy {item} online grocery delivery walmart instacart`, return top 3:

```json
{ "results": { "carrot": [ { "title": "...", "url": "...", "source": "walmart.com" } ] } }
```

Concurrent via `asyncio.gather`, capped at 8 items.

> **Optional upgrade if ahead of schedule:** SerpApi's `walmart` engine
> (`engine=walmart&query=...`) returns real products with prices and
> thumbnails. Your key is already wired. Stronger "Need" column — product
> cards instead of blue links. Costs extra searches; only if everything else
> is green.

---

## LLM extraction — completeness is the requirement

One call. `max_tokens: 8000` (detail objects are verbose; a truncated response
is the most likely failure mode here). Temperature 0.

### What to send

1. Video title and channel name.
2. `video_duration_seconds`.
3. The chapter list, if present, as a section outline.
4. **The entire transcript.** Every line, formatted `[142] text here` using
   seconds. Do not truncate, sample, or summarize before sending — a 20-minute
   video is roughly 3,000 words and fits comfortably. Truncating the transcript
   is the number one cause of missing the plating and finishing steps.

### What to demand

System prompt must require **raw JSON only** — no markdown fences, no
preamble. Parse defensively: strip ` ```json ` fences before `json.loads`.

**Completeness rules, stated explicitly in the prompt:**

- Cover the recipe **end to end**: prep, cooking, finishing, plating, garnish,
  serving. ASR transcripts trail off into outros and sponsor reads — ignore
  those, but do not stop early because of them.
- Produce **8–20 steps**. If the video is long or involved, use more steps
  rather than cramming several actions into one.
- Walk the transcript in order and account for every cooking action mentioned.
  A viewer following only these steps must be able to finish the dish without
  watching the video.
- `start_seconds` must come from an **actual transcript timestamp** — never
  invented. `end_seconds` is the next step's start, or video end for the last.
- Steps in strictly increasing timestamp order.
- Align step boundaries to chapters when chapters were provided.
- `narration` is a clean, spoken-aloud rewrite — ASR output is lowercase,
  unpunctuated, and full of filler.

**Detail rules:**

- Fill `detail` for any step involving a technique, a judgment call ("until
  golden"), or a step a beginner could get wrong.
- `how` — 2–4 sentences of concrete mechanics. Assume the reader has never
  done it.
- `watch_for` — the common mistake or failure signal.
- `duration_hint` — rough time, only when meaningful.
- `null` for trivial steps. Padding every step with detail makes the UI noise.

**Tag rules — pick from these fixed vocabularies only:**

- **Course (at least one, required):** Breakfast, Appetizer, Salad, Soup,
  Main, Side, Dessert, Snack, Drink, Sauce
- **Cuisine (at most one):** Italian, Mexican, Chinese, Japanese, Indian,
  Thai, Mediterranean, American, French, Korean, Middle Eastern, Other
- **Attributes (any number):** Vegetarian, Vegan, Gluten-Free, Quick,
  One-Pot, Baked, Grilled, No-Cook

Fixed vocabularies matter — free-form tags produce "dessert", "Desserts", and
"sweet treats" as three separate filters. Validate server-side and drop
anything off-list.

Ingredient `category` uses the same six as the pantry: Produce, Protein,
Dairy, Pantry, Spices, Other.

### Coverage validation (write this — it's the completeness guarantee)

After parsing, before returning, check:

1. **Monotonic** — timestamps strictly increasing. If not, sort by
   `start_seconds` and renumber `index`.
2. **Late start** — `first_step_at > 0.4 × duration` means the model skipped
   the opening prep.
3. **Early finish** — `last_step_at < 0.7 × duration` means it dropped the
   ending.
4. **Gap** — any adjacent pair more than `0.25 × duration` apart means a
   missing middle section.

If 2, 3, or 4 trips, make **one** repair call: send back the parsed steps plus
the transcript for the uncovered window only, and ask for the missing steps in
the same schema. Merge, re-sort, renumber. **One retry maximum** — then return
what you have. A slightly gappy recipe beats a spinner at the demo table.

Log which check failed so you can see it in Cloud Run logs.

Clamp every `start_seconds` to `max(0, value - 2)` so playback starts just
before the action.

---

## Frontend: five views, one page

Vanilla JS, no framework, no router. Toggle `hidden` on five `<div>`s.
Persistent top nav: **Cook · My Recipes · My Pantry**.

### View 0 — My Pantry

The standing list of what's in the house. Must be pleasant enough to maintain.

- **Grouped chip grid.** One section per category with header and count
  (`Produce · 6 of 9`). Ingredients are pill-shaped toggle chips in a wrapping
  flex row.
- **Chip states:** *Have* is filled amber with dark text and a check icon;
  *Out* is transparent with a muted border. Whole chip is the hit target,
  minimum 44px tall — this gets tapped on a phone in a kitchen.
- **Instant toggles.** Update state and repaint immediately, write to
  `localStorage` debounced 300ms. No spinners, no network.
- **Add item:** text input pinned at top, Enter or Add button. Lands in
  `Other`, appears immediately as *Have*. Trim + lowercase; if it already
  exists, toggle it instead of duplicating.
- **Per-section All / None** text buttons. Stocking a pantry shouldn't be 24
  clicks.
- **Header summary:** `18 of 24 stocked` with a thin progress bar.
- **Footer:** "Reset all data" — clears every `cookalong:*` key, with a confirm.
- Remove-item (× on hover) is low priority; *Out* covers most of the need.

### View 1 — Paste

URL input + "Load Recipe". Honest staged status: "Fetching transcript…" →
"Reading the recipe…". SerpApi returns in ~1s but the LLM call runs 15–25s
with detail objects. Never show a blank screen.

Below the input, the three most recent saved recipes as thumbnail cards for
one-click reopening.

### View 2 — Ingredient Check

**Stage A — unknowns only.** For each ingredient not in the pantry at all, one
large centered card:

> **Do you have sesame oil?**
> [ Yes, I have it ]   [ No, I need it ]

`3 of 5` above so the flow feels finite. Each answer writes straight to the
pantry in `localStorage` — answer once, never asked again. If nothing is
unknown, skip to Stage B. A returning user goes paste → cook with no questions.

**Stage B — two columns.**

- **Left: You have these** — green, checkmark chips.
- **Right: You need these** — amber. Each item a card with its `/api/shop`
  links beneath, opening in a new tab.
- Chips in both columns stay clickable and slide to the other column on
  toggle. People realize mid-check that they do have the ginger.
- Above: `You have 7 of 9 ingredients.`
- **"Start Cooking →"** always enabled. If Need isn't empty, a quiet line
  under it: *"You can start now and grab the rest as you go."*

Reuse the exact chip component from View 0. One component, three screens.

### View 3 — Cook Along

- **Top:** YouTube IFrame Player API embed, sticky on scroll.
  ```html
  <script src="https://www.youtube.com/iframe_api"></script>
  ```
  Build the player in `onYouTubeIframeAPIReady`; keep it in a module-scope
  `player`.
- **Header:** recipe title, channel name (clicking it filters the library to
  that channel), and tag chips.
- **Bottom:** numbered step cards.
  - Click a card → `player.seekTo(step.start_seconds, true)` then
    `player.playVideo()`.
  - Technique terms are inline `<button>`s, underlined and accented. Click
    seeks to `term.start_seconds` and **stops propagation** so the parent
    handler doesn't also fire.
  - **"How do I do this?"** disclosure on any step with a `detail` object.
    Expands in place to show `how`, then `watch_for` on a muted line with a
    small warning icon, then `duration_hint`. Collapsed by default — one open
    at a time. This is the depth layer; it must not clutter the default view.
  - `mm:ss` on the right of each card.
  - Active-step highlight: poll `player.getCurrentTime()` every 500ms, add a
    left accent border to the currently playing step, auto-scroll into view.
  - Checkbox per step, persisted to `cookalong:progress:v1`. Reopening a
    recipe restores tick marks.

### View 4 — My Recipes (library)

- Grid of cards: thumbnail, title, channel name, tag chips, step count.
- **Filter row 1 — tags.** Chips for every tag present across saved recipes,
  with counts (`Dessert 3`). Multi-select, AND-combined.
- **Filter row 2 — channels.** Chips for every distinct `channel.name`, with
  counts. Single-select.
- **Text search** across title, channel, and ingredient names.
- Empty state: "No recipes yet — paste a YouTube link to get started."
- Cards open straight from cached `localStorage` JSON. Zero API calls.

### Visual direction

Dark background, warm amber accent, generous spacing, large readable type —
this gets read from across a kitchen counter. System font stack. Ten minutes
on styling, no more.

---

## Secrets

Three keys: `ANTHROPIC_API_KEY`, `SERPAPI_KEY`, `TAVILY_API_KEY`.

- Read only via `os.environ`. Never hardcode, never log values.
- `.gitignore` with `.env`, `__pycache__/`, `sample_video.json`, `*.pyc` —
  written **before** the first commit.
- Commit `.env.example` with empty key names.
- Keys stay **server-side only**. The frontend talks only to `/api/*`. Grep
  `static/index.html` for key fragments before deploying.
- On Cloud Run pass with `--set-env-vars`. Never bake into the image.
- Missing key at startup → fail loudly, naming the variable.

No user auth, no CORS restrictions, no rate limiting — explicitly out of scope.
Note that `localStorage` is per-origin and per-browser: this is per-device
convenience, not private storage, and that's the right tradeoff here.

---

## File layout

```
cookalong/
├── main.py              # FastAPI: /api/recipe, /api/shop, static mount
├── serpapi_client.py    # both engines + parsing + duration derivation
├── prompts.py           # extraction prompt + repair prompt
├── validate.py          # coverage checks + merge/renumber
├── static/
│   └── index.html       # all five views, markup + Tailwind CDN + JS
├── sample_video.json    # cached demo fallback (gitignored)
├── requirements.txt
├── Dockerfile
├── .env.example
├── .gitignore
└── README.md
```

No `inventory.json`. The server holds no user state.

Seed pantry (defined as a JS constant in `index.html`, written to
`localStorage` on first visit only) — 24 staples so the grid looks populated
and the Have/Need split actually produces both columns:

- **Produce:** garlic, onion, ginger, lemon, carrot
- **Protein:** eggs, chicken breast
- **Dairy:** butter, milk, parmesan
- **Pantry:** rice, pasta, flour, sugar, soy sauce, olive oil, sesame oil,
  vinegar, canned tomatoes
- **Spices:** salt, black pepper, chili flakes, cumin, paprika

---

## Deployment

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
```

Cloud Run injects `PORT` — bind to it, never a hardcoded 8080.

```bash
gcloud run deploy cookalong \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars "ANTHROPIC_API_KEY=...,SERPAPI_KEY=...,TAVILY_API_KEY=..." \
  --memory 512Mi \
  --timeout 300
```

300s timeout matters — extraction plus a repair call can exceed the default.
Because the server is stateless, `--max-instances` and scaling need no thought.
Put both commands in `README.md`.

---

## Build order (timeboxed)

| Phase | Min | Deliverable |
|---|---|---|
| 1 | 15 | FastAPI skeleton; both SerpApi calls concurrent; parsed to `{seconds, text}` + metadata; raw responses cached to `sample_video.json` |
| 2 | 25 | LLM extraction with detail + tags; coverage validation and the one repair call; verified locally on a real video |
| 3 | 25 | Cook-along view: player, steps, **click-to-seek**, expandable detail |
| 4 | 15 | Pantry chip grid + ingredient check, all in `localStorage` |
| 5 | 10 | Library view with tag and channel filters |
| 6 | 10 | Tavily shopping links |
| 7 | 15 | Dockerfile, deploy, verify live URL |

**Phase 3 is the demo.** Under time pressure cut phase 6 first (hardcode
`https://www.walmart.com/search?q={item}`), then phase 5's text search. Never
cut click-to-seek or the coverage validation.

---

## Definition of done

- [ ] Paste a URL → recipe appears with title, channel, thumbnail, tags
- [ ] Steps span the video: last step lands in the final third, no gap larger
      than a quarter of the runtime
- [ ] Clicking step #4 jumps the video to step #4's moment
- [ ] Clicking "julienne" inside a step jumps to that technique
- [ ] "How do I do this?" expands with concrete mechanics on technique steps
- [ ] Pantry chips toggle, persist across reload, and survive a hard refresh
- [ ] Yes/No cards only appear for ingredients not already in the pantry
- [ ] Library filters by tag and by channel; cards reopen with no API calls
- [ ] Deployed Cloud Run URL works end to end
- [ ] `git status` shows no `.env` and no keys in tracked files

---

## Instructions to the agent

Work autonomously. No confirmation between phases. After each phase, run the
app and verify that phase's behavior yourself before moving on.

**Verify phase 2 against a real recipe video before building any UI on top of
it.** Print the parsed steps with timestamps and eyeball whether they cover the
whole cook. Everything downstream depends on that JSON being complete; finding
out at minute 80 that steps stop two-thirds through is the failure mode this
spec is built to prevent.

If an API fails, fall back to cached data and keep moving — a working demo with
one stubbed component beats a broken demo with none.

When finished, report: the Cloud Run URL, which phases are fully working, and
anything you stubbed out.