FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY requirements.txt constraints.txt ./
RUN --mount=type=cache,target=/root/.cache/pip python -m pip install -r requirements.txt

COPY alembic.ini ./
COPY migrations/ ./migrations/
COPY scripts/ ./scripts/
COPY src/ ./src/

RUN mkdir /app/data && chown 1000:1000 /app/data
USER 1000:1000

CMD ["sh", "-c", "python -m alembic upgrade head && exec python -m uvicorn src.main:app --host 0.0.0.0 --port 8000"]
