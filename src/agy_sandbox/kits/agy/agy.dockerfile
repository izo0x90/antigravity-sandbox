# syntax=docker/dockerfile:1
# The binary overlay is Docker's published Antigravity mixin (agy installed under
# /home/agent/.local by Google's installer). Only the descriptor is ours; a kit used
# in FROM contributes files, never capabilities.
FROM docker.io/sbx/antigravity-mixin:latest
