# syntax=docker/dockerfile:1
# Overlay: the Codex CLI exactly as Docker's published codex mixin lays it out,
# under the agent's home with the same launcher symlinks. A kit's FROM contributes
# nothing to the overlay (only the layers a recipe adds are shipped), so the
# payload is copied out of the mixin explicitly. Staged under /out and chowned
# there so /home itself stays root-owned.
ARG BASE_IMAGE=docker.io/sbx/shell-image:latest
FROM docker.io/sbx/codex-mixin:latest AS mixin
FROM ${BASE_IMAGE} AS build
USER root
COPY --from=mixin /home/agent/.codex /out/home/agent/.codex
COPY --from=mixin /etc/profile.d/codex-env.sh /out/etc/profile.d/codex-env.sh
RUN set -eu; \
    mkdir -p /out/home/agent/.local/bin /out/usr/local/bin /out/opt; \
    ln -s /home/agent/.codex/packages/standalone/current/bin/codex /out/home/agent/.local/bin/codex; \
    ln -s /home/agent/.local/bin/codex /out/usr/local/bin/codex; \
    ln -s /home/agent/.codex/packages/standalone/current /out/opt/codex; \
    chown -R 1000:1000 /out/home/agent

FROM scratch
COPY --from=build /out /
