# syntax=docker/dockerfile:1
# Overlay: the relay script under the agent's home. Staged under /out and chowned
# there so /home itself stays root-owned (an overlay's directory entries override
# the base's, owner included).
ARG BASE_IMAGE=docker.io/sbx/shell-image:latest
FROM ${BASE_IMAGE} AS build
USER root
COPY chrome_host_relay.py /tmp/chrome_host_relay.py
RUN set -eu; \
    mkdir -p /out/home/agent/.local/share/agy-sandbox; \
    install -m 0644 /tmp/chrome_host_relay.py /out/home/agent/.local/share/agy-sandbox/chrome_host_relay.py; \
    chown -R 1000:1000 /out/home/agent

FROM scratch
COPY --from=build /out /
