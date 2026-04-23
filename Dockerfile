FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY scripts ./scripts

ENV PYTHONPATH=/app

EXPOSE 8000

CMD ["sh", "scripts/docker_start.sh"]
