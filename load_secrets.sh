#!/usr/bin/env bash
# Load the three API keys from .env into Secret Manager.
#
# Each value is piped on STDIN, never passed as an argument, so no key value
# reaches shell history, the process table, or the gcloud audit log.
set -euo pipefail

[ -f .env ] || { echo "No .env file. Copy .env.example and fill it in." >&2; exit 1; }

put() {                       # put <ENV_VAR_NAME> <secret-name>
  local var="$1" secret="$2" value
  value="$(grep -E "^${var}=" .env | head -1 | cut -d= -f2- | tr -d '\r\n')"
  if [ -z "$value" ]; then
    echo "skip  ${var} is empty in .env"
    return
  fi
  if gcloud secrets describe "$secret" >/dev/null 2>&1; then
    printf '%s' "$value" | gcloud secrets versions add "$secret" --data-file=- >/dev/null
    echo "ok    ${secret} (new version)"
  else
    printf '%s' "$value" | gcloud secrets create "$secret" --data-file=- \
      --replication-policy=automatic >/dev/null
    echo "ok    ${secret} (created)"
  fi
}

put OPENAI_API_KEY openai-api-key
put GEMINI_API_KEY gemini-api-key
put SERPAPI_KEY    serpapi-key
put TAVILY_API_KEY tavily-key

# Cloud Run's runtime service account needs read access to each secret.
project="$(gcloud config get-value project 2>/dev/null)"
number="$(gcloud projects describe "$project" --format='value(projectNumber)')"
member="serviceAccount:${number}-compute@developer.gserviceaccount.com"

for secret in openai-api-key gemini-api-key serpapi-key tavily-key; do
  gcloud secrets describe "$secret" >/dev/null 2>&1 || continue
  gcloud secrets add-iam-policy-binding "$secret" \
    --member="$member" --role=roles/secretmanager.secretAccessor >/dev/null
  echo "ok    ${secret} readable by Cloud Run"
done

echo
echo "Done. Nothing above printed a key value."
