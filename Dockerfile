FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr libglib2.0-0 libgomp1 gosu \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

# Non-root runtime user. Everything up to here (apt, uv install) still runs
# as root, which the image needs to install system packages; nothing from
# this point on does except the entrypoint's brief ownership fix (see
# docker-entrypoint.sh) -- the actual application process always ends up
# running as appuser, never root. Home directory matches the venv/cache
# paths `uv` and `spacy`/HuggingFace touch at runtime so writes there don't
# need root either.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser

WORKDIR /srv

# Copy only the dependency manifests first so `uv sync` is a cacheable layer —
# it only re-runs when dependencies actually change, not on every source edit.
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --no-install-project

COPY . .
RUN uv sync --no-dev

# /data/documents is the default STORAGE_LOCAL_PATH (see docker-compose.yml's
# document_data volume mount) -- appuser needs to write scans there. This
# only sets ownership for a *fresh* volume (or no volume at all); an
# existing volume created under an older, root-running version of this
# image is fixed at container start by docker-entrypoint.sh instead, since
# `docker compose up` reuses existing volume content (and its ownership)
# as-is rather than re-applying whatever the image itself has here.
RUN mkdir -p /data/documents \
    && chown -R appuser:appuser /srv /data/documents

ENV PATH="/srv/.venv/bin:$PATH"

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "review_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
