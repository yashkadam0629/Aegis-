FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 curl && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 aegis
WORKDIR /app
COPY backend/pyproject.toml backend/pyproject.toml
COPY backend/app backend/app
RUN pip install --no-cache-dir -e "backend/.[postgres]"
COPY migrations migrations
COPY alembic.ini .
RUN mkdir -p /app/data /app/artifacts && chown -R aegis:aegis /app
USER aegis
ENV AEGIS_DATA_DIR=/app/data AEGIS_ARTIFACT_DIR=/app/artifacts
WORKDIR /app/backend
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 CMD curl -fsS http://localhost:8000/api/health || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
