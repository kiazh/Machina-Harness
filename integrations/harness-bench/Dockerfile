FROM pgvector/pgvector:pg16

# The upstream PostgreSQL image may select its unprivileged account by default.
# Package installation and creation of the fixed UID/GID must run as root.
USER root

# The pgvector/pg16 image is assumed to provide PostgreSQL 16 server/client and
# pgvector. The remaining task tools are installed explicitly so the harness
# does not depend on tools leaked from the controller image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       bash ca-certificates curl git passwd python3 python3-pip ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 machina \
    && useradd --uid 1000 --gid 1000 --create-home --home-dir /home/machina \
       --shell /bin/bash machina \
    && mkdir -p /opt/harness/bin \
    && chown -R 1000:1000 /opt/harness /home/machina

ENV PATH="/usr/lib/postgresql/16/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY docker/harnesses/machina/machina-*.whl /tmp/
RUN python3 -m pip install --break-system-packages --no-cache-dir /tmp/machina-*.whl \
    && rm -f /tmp/machina-*.whl

COPY docker/harnesses/machina/entrypoint.sh /opt/harness/bin/run
RUN chmod 0755 /opt/harness/bin/run

USER 1000:1000
ENTRYPOINT ["/opt/harness/bin/run"]
