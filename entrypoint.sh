#!/bin/sh
set -e
# Apply DB migrations, then launch the app (single worker: in-process scheduler).
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
