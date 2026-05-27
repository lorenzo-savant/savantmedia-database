# ════════════════════════════════════════════════════════════════
# backend/Dockerfile — FastAPI orchestrator (Python 3.12)
# ────────────────────────────────────────────────────────────────
# Build (da repo root):
#   docker build -f backend/Dockerfile -t savantsdatabas-backend ./backend
#
# Run:
#   docker run --rm -p 8000:8000 --env-file backend/.env \
#     savantsdatabas-backend
#
# NOTA: `playwright install chromium` NON viene eseguito qui — pesa
# ~150 MB e non serve fino a quando T4/T5 sono attivati. Aggiungilo
# nell'entrypoint o in un Dockerfile derivato se ti servono:
#   RUN python -m playwright install --with-deps chromium
# ════════════════════════════════════════════════════════════════


FROM python:3.12-slim AS base

# Env Python sane defaults
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build deps per:
#   - lxml/trafilatura → libxml2-dev libxslt1-dev
#   - psycopg / pg client (se mai aggiunto) → libpq-dev
#   - crawl4ai usa Playwright; le sue native deps si installano solo
#     quando lanci `playwright install --with-deps`
#   - curl per healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libxml2-dev \
        libxslt1-dev \
        libpq-dev \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*


# ─── Layer deps ──────────────────────────────────────────────────
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt


# ─── Copia sorgenti ──────────────────────────────────────────────
COPY . /app


# ─── Utente non-root ─────────────────────────────────────────────
RUN groupadd --system --gid 1001 savant \
  && useradd  --system --uid 1001 --gid savant --create-home savant \
  && chown -R savant:savant /app
USER savant

EXPOSE 8000

# Healthcheck colpisce /health (esposto da api/main.py).
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl --fail --silent http://localhost:8000/health || exit 1

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
