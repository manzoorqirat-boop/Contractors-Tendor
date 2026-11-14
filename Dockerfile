# Stage 1: build the web page
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# Stage 2: the Python service, serving that page
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 STATIC_DIR=/app/web/dist STATE_PATH=/data/tender_state.db
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY tender_digest ./tender_digest
COPY --from=web /web/dist ./web/dist
# Railway sets PORT. One process only: the scheduler and the run lock live inside it.
CMD ["python", "-m", "tender_digest.web"]
