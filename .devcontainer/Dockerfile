FROM ghcr.io/astral-sh/uv:latest AS uv

FROM mcr.microsoft.com/devcontainers/python:dev-3.14

COPY --from=uv --chown=vscode: /uv /uvx /bin/
