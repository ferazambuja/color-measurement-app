# Build x64 with --platform linux/amd64. Debian 12 fixes the glibc baseline at 2.36.
FROM node:22-bookworm-slim@sha256:3ed748ce9dea62d2f0f0205cdbb6823106d9f973a6f555c402fe2575f8d6e1ae
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv python3-dev build-essential clang ca-certificates curl \
    libegl1 libopengl0 libxcb-cursor0 libxkbcommon-x11-0 libnss3 libasound2 \
    libxcomposite1 libxdamage1 libxrandr2 libxtst6 libxi6 libxss1 libxshmfence1 \
    libgl1 libglib2.0-0 libdbus-1-3 libfontconfig1 \
    libxcb-icccm4 libxcb-keysyms1 libxcb-shape0 xvfb xauth \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/uv \
    && /opt/uv/bin/pip install --no-cache-dir uv==0.12.15 \
    && useradd --create-home --uid 1001 builder
ENV PATH="/opt/uv/bin:${PATH}" UV_PROJECT_ENVIRONMENT=/tmp/cm-venv QT_QPA_PLATFORM=offscreen
USER builder
WORKDIR /project
