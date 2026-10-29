# syntax=docker/dockerfile:1

# ---------- Stage 1: build the React single-page app ----------
FROM node:22-slim AS web
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- Stage 2: Flask API + built SPA served from one origin ----------
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY schema_mysql.sql README.md ./
COPY --from=web /app/frontend/dist ./frontend/dist

RUN useradd --create-home --uid 10001 roam \
 && mkdir -p backend/instance \
 && chown -R roam:roam /app
USER roam

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/api/health', timeout=4)"

# One worker keeps the SQLite demo database and the in-process rate limiter coherent;
# Gunicorn threads still serve concurrent requests. Use MySQL to scale out.
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT} --workers 1 --threads 4 --worker-tmp-dir /dev/shm --access-logfile - --error-logfile - 'backend.app:create_app()'"]
