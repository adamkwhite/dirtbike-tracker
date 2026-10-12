FROM python:3.13-slim

# supercronic: cron for containers (passes env through, logs to stdout)
ARG SUPERCRONIC_URL=https://github.com/aptible/supercronic/releases/download/v0.2.49/supercronic-linux-amd64
ARG SUPERCRONIC_SHA1SUM=e63c11a9726b775a6a11801e81af4f3fb926aa68
ADD --chmod=755 ${SUPERCRONIC_URL} /usr/local/bin/supercronic
RUN echo "${SUPERCRONIC_SHA1SUM}  /usr/local/bin/supercronic" | sha1sum -c -

# rclone for `dirtbike backup`, tzdata so cron hours are Ontario time
RUN apt-get update \
    && apt-get install -y --no-install-recommends rclone tzdata \
    && rm -rf /var/lib/apt/lists/*

# Same uid as the host user, so bind-mounted data/ stays writable on both sides
ARG UID=1000
RUN useradd --create-home --uid ${UID} app

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
# Editable: the CLI resolves data/, config/, reports/ relative to the source tree
RUN pip install --no-cache-dir -e . \
    && mkdir -p data/state reports site \
    && ln -s site/dashboard.html dashboard.html \
    && chown -R app:app data reports site
COPY docker/crontab ./docker/crontab

USER app
ENV TZ=America/Toronto PYTHONUNBUFFERED=1
CMD ["supercronic", "/app/docker/crontab"]
