# syntax=docker/dockerfile:1
# Overlay: Prime Agent as installed by its own installer (a self-contained tree under
# ~/.local/share/prime-agent and a launcher in ~/.local/bin). The install runs as the
# agent user on the shell base, then the per-user prefix is staged under /out and
# chowned there so /home itself stays root-owned in the overlay.
ARG BASE_IMAGE=docker.io/sbx/shell-image:latest
FROM ${BASE_IMAGE} AS build
USER agent
RUN set -eu; \
    export PRIME_AGENT_INSTALLER_PLAIN=1 PRIME_AGENT_BOOTSTRAP_KERNEL_ON_INSTALL=0; \
    curl -fsSL https://app.primeintellect.ai/prime-agent/install.sh | sh; \
    test -x /home/agent/.local/bin/prime-agent; \
    PATH="/home/agent/.local/bin:$PATH" prime-agent --version >/dev/null 2>&1 || true

USER root
RUN set -eu; \
    mkdir -p /out/home/agent/.local/bin /out/home/agent/.local/share; \
    cp -a /home/agent/.local/share/prime-agent /out/home/agent/.local/share/prime-agent; \
    cp -a /home/agent/.local/bin/prime-agent /out/home/agent/.local/bin/prime-agent; \
    mkdir -p /out/usr/local/bin; \
    printf '#!/bin/sh\nexec /home/agent/.local/bin/prime-agent "$@"\n' > /out/usr/local/bin/prime-agent; \
    chmod 0755 /out/usr/local/bin/prime-agent; \
    chown -R 1000:1000 /out/home/agent

FROM scratch
COPY --from=build /out /
