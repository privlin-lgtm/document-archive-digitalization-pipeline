FROM python:3.11-slim

RUN pip install --no-cache-dir uv

WORKDIR /srv

COPY pyproject.toml ./
COPY . .

RUN uv sync --no-dev

ENV PATH="/srv/.venv/bin:$PATH"

EXPOSE 8000

CMD ["uvicorn", "review_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
