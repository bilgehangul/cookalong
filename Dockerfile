FROM python:3.11-slim

# Hugging Face Spaces runs containers as uid 1000, and the app writes
# sample_video.json into its own directory, so own the tree as that user.
RUN useradd -m -u 1000 user
WORKDIR /app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user . .
USER user

# HF Spaces expects 7860; Cloud Run injects PORT. Bind whichever is present.
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-7860}
