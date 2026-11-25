# syntax=docker/dockerfile:1
# Overlay: Claude Code laid out the way Anthropic's native installer manages it,
# installed as the agent user: ~/.local/bin/claude is a symlink into
# ~/.local/share/claude/versions/<v>. That is the layout `claude update` and the
# background auto-updater maintain, so updates inside the sandbox replace the copy
# that is first on PATH. (Docker's claude mixin ships a root-owned binary in
# /usr/local/bin, which the updater cannot replace, and a kit's FROM contributes
# nothing to the overlay anyway: only the layers a recipe adds are shipped.)
# Staged under /out and chowned there so /home itself stays root-owned.
ARG BASE_IMAGE=docker.io/sbx/shell-image:latest
ARG VERSION=latest
FROM ${BASE_IMAGE} AS build
ARG VERSION
USER agent
RUN set -eu; \
    curl -fsSL https://claude.ai/install.sh | bash -s "$VERSION"; \
    test -L /home/agent/.local/bin/claude; \
    /home/agent/.local/bin/claude --version
USER root
RUN set -eu; \
    mkdir -p /out/home/agent/.local/bin /out/home/agent/.local/share /out/usr/local/bin; \
    cp -a /home/agent/.local/share/claude /out/home/agent/.local/share/claude; \
    cp -a /home/agent/.local/bin/claude /out/home/agent/.local/bin/claude; \
    printf '#!/bin/sh\nexec /home/agent/.local/bin/claude "$@"\n' > /out/usr/local/bin/claude; \
    chmod 0755 /out/usr/local/bin/claude; \
    chown -R 1000:1000 /out/home/agent

FROM scratch
COPY --from=build /out /
