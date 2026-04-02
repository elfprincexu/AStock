FROM python:3.12-slim

# System dependencies for psycopg2, scipy, lightgbm, lxml (akshare dep)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libpq-dev libxml2-dev libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps (cached layer)
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend application code
COPY backend/ .

# Copy scripts and data (for download_all_data.py etc.)
COPY scripts/ /app/scripts/
COPY data/ /app/data/

# Default: run FastAPI
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
