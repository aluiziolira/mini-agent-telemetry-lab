FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY . /app

RUN uv sync --frozen

CMD ["uv", "run", "python", "manage.py", "runserver", "0.0.0.0:8000"]
