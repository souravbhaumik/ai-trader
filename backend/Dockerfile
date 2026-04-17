FROM python:3.11-slim

# Install system deps (pg_isready for healthchecks, gcc for some wheels)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m spacy download en_core_web_sm

# Copy source
COPY . .

EXPOSE 8000
