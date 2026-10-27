# ComplyAgent in one image: the React app is built, then served by the FastAPI backend.
#
#   docker build -t complyagent .
#   docker run -p 8000:8000 --env-file backend/.env complyagent
#
# The same image runs the scheduled jobs on Azure (deploy/azure): only the command changes,
# e.g. python -m app.manage run-follow-ups.

FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STORAGE_DIR=/app/storage \
    FRONTEND_DIR=/app/web \
    APP_ENV=production
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# The embedding model is downloaded at build time, so a new server starts ready.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/app/storage/models')"
COPY backend/app ./app
COPY backend/models ./models
COPY sample_policies /app/sample_policies
COPY --from=web /web/dist /app/web
RUN useradd --create-home --uid 10001 complyagent && chown -R complyagent /app
USER complyagent
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
