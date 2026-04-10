set dotenv-load := true

setup:
    uv run python manage.py migrate
    DJANGO_SUPERUSER_USERNAME=admin DJANGO_SUPERUSER_EMAIL=admin@example.com DJANGO_SUPERUSER_PASSWORD=admin uv run python manage.py createsuperuser --noinput || true

agent:
    uv run python demo_agent.py "Should I buy AAPL?"

eval:
    uv run python manage.py eval_pending

worker:
    uv run python manage.py run_huey

test:
    uv run pytest
