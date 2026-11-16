# Stage 1: build the web page
FROM node:22-bookworm-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# Stage 2: the Python service, serving that page
FROM python:3.12-slim-bookworm
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 STATIC_DIR=/app/web/dist STATE_PATH=/data/tender_state.db
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
 && useradd --system --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin app
COPY tender_digest ./tender_digest
COPY --from=web /web/dist ./web/dist
COPY docker-entrypoint.sh healthcheck.py ./
RUN chmod 0555 docker-entrypoint.sh
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 CMD ["python", "healthcheck.py"]
# The entrypoint fixes the data folder's owner and then runs the command below as the unprivileged "app" user.
# One process only: the scheduler and the run lock live inside it.
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "-m", "tender_digest.web"]
