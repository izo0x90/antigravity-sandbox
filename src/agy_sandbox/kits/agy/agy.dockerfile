# syntax=docker/dockerfile:1
# Overlay: Google's agy CLI exactly as Docker's published antigravity mixin lays
# it out, under the agent's home with the same launcher symlink. A kit's FROM
# contributes nothing to the overlay (only the layers a recipe adds are shipped),
# so the payload is copied out of the mixin explicitly. Staged under /out and
# chowned there so /home itself stays root-owned.
ARG BASE_IMAGE=docker.io/sbx/shell-image:latest
FROM docker.io/sbx/antigravity-mixin:latest AS mixin
FROM ${BASE_IMAGE} AS build
USER root
COPY --from=mixin /home/agent/.local/bin/agy /out/home/agent/.local/bin/agy
COPY --from=mixin /etc/profile.d/antigravity-env.sh /out/etc/profile.d/antigravity-env.sh
RUN set -eu; \
    mkdir -p /out/usr/local/bin; \
    ln -s /home/agent/.local/bin/agy /out/usr/local/bin/agy; \
    chown -R 1000:1000 /out/home/agent

FROM scratch
COPY --from=build /out /
