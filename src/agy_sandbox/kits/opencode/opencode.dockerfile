# syntax=docker/dockerfile:1
# Overlay: OpenCode exactly as Docker's published opencode mixin lays it out,
# under the agent's home with the same launcher symlinks. A kit's FROM contributes
# nothing to the overlay (only the layers a recipe adds are shipped), so the
# payload is copied out of the mixin explicitly. Staged under /out and chowned
# there so /home itself stays root-owned.
ARG BASE_IMAGE=docker.io/sbx/shell-image:latest
FROM docker.io/sbx/opencode-mixin:latest AS mixin
FROM ${BASE_IMAGE} AS build
USER root
COPY --from=mixin /home/agent/.opencode /out/home/agent/.opencode
RUN set -eu; \
    mkdir -p /out/usr/local/bin /out/opt/opencode/bin; \
    ln -s /home/agent/.opencode/bin/opencode /out/usr/local/bin/opencode; \
    ln -s /home/agent/.opencode/bin/opencode /out/opt/opencode/bin/opencode; \
    chown -R 1000:1000 /out/home/agent

FROM scratch
COPY --from=build /out /
