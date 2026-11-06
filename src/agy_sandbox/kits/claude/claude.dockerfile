# syntax=docker/dockerfile:1
# The binary overlay is Docker's published Claude Code mixin. Only the descriptor
# is ours; a kit used in FROM contributes files, never capabilities.
FROM docker.io/sbx/claude-mixin:latest
