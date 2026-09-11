FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .

EXPOSE 8000

# Most PaaS platforms (Render, Railway, ...) inject $PORT and expect the app to listen on it.
# Migrations run once per container start, before the app accepts traffic -- see alembic/env.py,
# which reads DATABASE_URL from the same Settings the app itself uses.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
