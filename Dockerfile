FROM python:3.12-slim

# Tesseract runs as a local process inside the container - no network needed at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ocr.py checkers.py app.py ./
COPY static ./static

ENV PORT=8000 \
    OMP_THREAD_LIMIT=1
EXPOSE 8000
# Azure App Service reads WEBSITES_PORT (set it to 8000); Render injects PORT.
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]