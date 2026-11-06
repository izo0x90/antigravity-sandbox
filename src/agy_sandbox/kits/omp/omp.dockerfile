# syntax=docker/dockerfile:1
# Overlay: the omp binary. Built in a stage on the shell base and staged under /out so
# the final layer carries exactly one file at its final path.
ARG BASE_IMAGE=docker.io/sbx/shell-image:latest
FROM ${BASE_IMAGE} AS build
ARG TARGETARCH
ARG OMP_VERSION=v17.2.8
USER root
RUN set -eu; \
    arch=$([ "${TARGETARCH:-arm64}" = arm64 ] && echo arm64 || echo x64); \
    mkdir -p /out/usr/local/bin; \
    curl -fsSL "https://github.com/can1357/oh-my-pi/releases/download/${OMP_VERSION}/omp-linux-${arch}" -o /out/usr/local/bin/omp; \
    chmod 0755 /out/usr/local/bin/omp

FROM scratch
COPY --from=build /out /
