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
| `ANTHROPIC_API_KEY` | recipe extraction (`claude-sonnet-5`) | pay-as-you-go |
| `SERPAPI_KEY` | video metadata + transcript | 250 searches/month, **2 per recipe** |
| `TAVILY_API_KEY` | shopping links (optional) | falls back to a Walmart search |

Read only via `os.environ`, never logged, never sent to the browser — the frontend
talks only to `/api/*`. A missing required key is named loudly in the logs at
startup and returned as a 503 naming the variable.

## Deploy to Cloud Run

One-time project setup:

```bash
gcloud projects create cookalong-demo
gcloud billing projects link cookalong-demo --billing-account=<OPEN_BILLING_ACCOUNT_ID>
gcloud config set project cookalong-demo
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com
```

Load the keys into Secret Manager. `./load_secrets.sh` pipes each value from `.env`
on stdin, so no key value ever appears in a command argument, in shell history, or
in the gcloud audit log:

```bash
./load_secrets.sh
```

Deploy:

```bash
gcloud run deploy cookalong \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-secrets "ANTHROPIC_API_KEY=anthropic-api-key:latest,SERPAPI_KEY=serpapi-key:latest,TAVILY_API_KEY=tavily-key:latest" \
  --memory 512Mi \
  --timeout 300
```

The 300s timeout matters: extraction plus a repair call can exceed the default.
Because the server is stateless, scaling needs no thought.

`.gcloudignore` must stay in the repo. Without it `gcloud run deploy --source .`
falls back to `.gitignore`, which would strip `sample_video.json` out of the image
and break the `?demo=1` fallback in production.

## Layout

```
main.py             FastAPI: /api/recipe, /api/shop, /api/health, static mount
serpapi_client.py   both SerpApi engines via asyncio.gather; ms -> s at parse time
llm.py              extraction + the single repair call (structured outputs)
prompts.py          prompts, tag vocabularies, and the JSON schema
validate.py         the four coverage checks, merge/re-sort/renumber
static/index.html   all five views, Tailwind CDN, vanilla JS, localStorage
```
