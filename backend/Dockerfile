FROM python:3.14-slim

WORKDIR /app

# Install system deps for asyncpg, and pg_dump/pg_restore for the server-side
# database backups (DB_BACKUP_DIR). The client comes from the PostgreSQL apt
# repository so its major version matches the server's (the shipped compose
# runs PostgreSQL 16): a newer pg_dump writes archives an older pg_restore
# cannot read.
ARG PG_CLIENT_MAJOR=16
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    ca-certificates \
    curl \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
       https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && . /etc/os-release \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
       > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends postgresql-client-${PG_CLIENT_MAJOR} \
    && rm -rf /var/lib/apt/lists/*

# Dependencies come from uv.lock (the source of truth — see scripts/lock.sh).
# The export never touches the lock (--frozen); pip then installs with hash
# checking, so the image only ever contains the exact audited versions. uv is
# removed afterwards — it is a build tool, not a runtime dependency.
# This layer is cached until pyproject.toml or uv.lock change.
COPY pyproject.toml uv.lock ./
# renovate: datasource=pypi depName=uv
RUN pip install --no-cache-dir uv==0.12.23 \
    && uv export --frozen --no-emit-project -o /tmp/requirements.txt \
    && pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
    && pip uninstall -y uv \
    && rm /tmp/requirements.txt

COPY . .
# The project itself: deps are already satisfied above, so --no-deps keeps
# this layer from re-resolving anything outside the lock.
RUN pip install --no-cache-dir --no-deps -e .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
