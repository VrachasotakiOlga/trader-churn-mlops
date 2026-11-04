# Stage 1: install dependencies, using uv. Only stage 2 ends up in the final image.
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv
WORKDIR /app

# Dependencies go first: Docker reuses this layer until the lock file changes, so
# changing only our code doesn't reinstall everything.
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --locked --no-dev --no-default-groups --no-install-project

COPY src ./src
RUN uv sync --locked --no-dev --no-default-groups --no-editable

# Stage 2: the image that actually runs, without uv or build leftovers.
FROM python:3.12-slim
WORKDIR /app
COPY --from=build /app/.venv ./.venv

# If an attacker broke in, they'd have no admin rights inside the container.
RUN useradd --system --no-create-home --uid 1000 app
USER 1000

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# The model isn't in the image: it's provided at /models when the container starts.
CMD ["uvicorn", "churn.serve:app", "--host", "0.0.0.0", "--port", "8000"]
