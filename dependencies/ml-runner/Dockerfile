FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN useradd --create-home --uid 1001 runner

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY runner.py server.py ./

USER runner

EXPOSE 8080

# gunicorn worker timeout must exceed the per-run ML timeout.
CMD ["gunicorn", "-b", "0.0.0.0:8080", "-w", "2", "-t", "140", "server:app"]
