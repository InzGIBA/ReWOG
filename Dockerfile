# Build stage: compile the package and its dependencies into a venv
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Dependencies layer: cached until the metadata changes
RUN uv venv /opt/venv --python "$(command -v python3)" \
    && uv pip install --python /opt/venv/bin/python --no-cache .

# Runtime stage: the venv plus nothing else
FROM python:3.12-slim

RUN groupadd --gid 1000 wog \
    && useradd --uid 1000 --gid wog --create-home wog

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}" \
    WOG_BASE_DIR=/data

# All runtime state (database, bundles, scenes) lives in the mounted
# volume; SQLite refuses a symlinked database path, so bind a real dir
RUN mkdir -p /data && chown wog:wog /data
VOLUME ["/data"]

USER wog
WORKDIR /data

ENTRYPOINT ["wog-dump"]
CMD ["--help"]
