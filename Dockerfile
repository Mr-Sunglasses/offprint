# syntax=docker/dockerfile:1

# ---------- front end ----------
FROM node:22-slim AS web
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
# vite's outDir is ../src/bpc_web/static, relative to web/ — same layout as the repo
RUN mkdir -p /app/src/bpc_web && npm run build

# ---------- runtime ----------
FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    BPC_SITES_JS=/data/sites.js

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
COPY src ./src
RUN uv sync --frozen --no-dev

# Chromium + its system libraries (needed both for the local browser and the
# standalone browser service built from this same image). libatomic1 is required
# by Playwright's driver node binary and isn't pulled in by --with-deps.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libatomic1 \
    && rm -rf /var/lib/apt/lists/* \
    && uv run playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY --from=web /app/src/bpc_web/static ./src/bpc_web/static
COPY docker/start-browser.sh /usr/local/bin/start-browser.sh
COPY docker/cdp_proxy.py /usr/local/bin/cdp_proxy.py
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/start-browser.sh /usr/local/bin/entrypoint.sh \
    && mkdir -p /data

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uv", "run", "bpc-web", "--host", "0.0.0.0", "--port", "8000"]
