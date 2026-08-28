# CookAlong

Turns a YouTube recipe video into a cook-along interface: video pinned at the top,
steps below, and clicking any step seeks the video to that moment.

All user data (pantry, saved recipes, step progress) lives in the browser's
`localStorage`. The server is stateless apart from an in-memory cache of parsed
recipes keyed by `video_id`.

## Run locally

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Linux/macOS: .venv/bin/python
cp .env.example .env                                      # then fill in your three keys
.venv/Scripts/python -m uvicorn main:app --reload --port 8080
```

Open <http://127.0.0.1:8080>.

`?demo=1` replays the cached responses in `sample_video.json` instead of calling
SerpApi, for when the venue wifi fails. That file is written automatically after
the first successful fetch.

## Keys

| Variable | Used for | Free tier |
|---|---|---|
| `OPENAI_API_KEY` | recipe extraction (primary, `gpt-4.1`) | your OpenAI credits |
| `GEMINI_API_KEY` | recipe extraction (automatic fallback, `gemini-2.5-flash`) | free tier |
| `SERPAPI_KEY` | video metadata + transcript | 250 searches/month, **2 per recipe** |
| `TAVILY_API_KEY` | shopping links (optional) | falls back to a Walmart search |

Read only via `os.environ`, never logged, never sent to the browser — the frontend
talks only to `/api/*`. A missing required key is named loudly in the logs at
startup and returned as a 503 naming the variable.

## Architecture

The frontend and backend are deployed separately, because no free Google Cloud
product will host a Python backend:

- **Frontend** -> Firebase Hosting (free Spark plan): <https://cookalong.web.app>
- **Backend** -> Hugging Face Spaces (free, Docker): the FastAPI app

`static/config.js` holds a single line naming the API base. `deploy_firebase.py`
rewrites it at deploy time, so the same `index.html` works locally (same origin,
empty base) and in production (cross-origin to the Space).

Cloud Run and App Engine were both ruled out empirically - each returns
`INVALID_ARGUMENT: The project must have a billing account attached`. Firebase
Hosting works billing-free only because it serves static files.

## Deploy the frontend

```bash
python deploy_firebase.py --api-base https://<your-space>.hf.space
```

Uses your existing gcloud credentials via the Hosting REST API - no interactive
`firebase login` and no CI token needed.

## Deploy the backend

The Space builds from the `Dockerfile` at the repo root, which binds
`${PORT:-7860}`. Set the four keys as **Space secrets** in the Hugging Face UI
(Settings -> Variables and secrets) so they are never committed anywhere:

```
OPENAI_API_KEY   GEMINI_API_KEY   SERPAPI_KEY   TAVILY_API_KEY
```

Optionally set `COOKALONG_ALLOWED_ORIGINS=https://cookalong.web.app` to restrict
CORS to the deployed frontend.

## Layout

```
main.py             FastAPI: /api/recipe, /api/shop, /api/health, static mount
serpapi_client.py   both SerpApi engines via asyncio.gather; ms -> s at parse time
llm.py              extraction + repair call; OpenAI primary, Gemini fallback
prompts.py          prompts, tag vocabularies, and the JSON schema
validate.py         the four coverage checks, merge/re-sort/renumber
static/index.html   all five views, Tailwind CDN, vanilla JS, localStorage
```
