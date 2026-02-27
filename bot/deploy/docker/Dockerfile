# Vikingbot 本地部署专用 Dockerfile
# 固定 linux/amd64（openviking 无 linux/aarch64 wheel）

FROM python:3.13-slim-bookworm

# Install system dependencies + Node.js 20 via NodeSource (npm/npx 自带，无需多阶段复制)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates git ripgrep bubblewrap socat libfuse2 build-essential gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    curl -LsSf https://astral.sh/uv/install.sh | sh && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

ENV PATH="/root/.local/bin:$PATH"
ENV PIP_BREAK_SYSTEM_PACKAGES=1
ENV UV_HTTP_TIMEOUT=300

WORKDIR /app

# ============================================
# Layer 1: Install Python dependencies (cached)
# ============================================
COPY pyproject.toml ./
RUN mkdir -p vikingbot

# Pre-install openviking from pip (linux/amd64 has pre-built wheel)
RUN uv pip install --system --no-cache "openviking>=0.1.18"

# Install npm sandbox runtime
RUN npm install -g @anthropic-ai/sandbox-runtime

# ============================================
# Layer 2: Install bridge npm deps (cached)
# ============================================
COPY bridge/package*.json ./bridge/
RUN cd bridge && npm install

# ============================================
# Layer 3: Copy source and install package
# ============================================
COPY vikingbot/ vikingbot/
COPY README.md ./
COPY license/LICENSE ./LICENSE
COPY bridge/ bridge/

# Install local vikingbot-ai package
RUN uv pip install --system --no-cache .

# Build WhatsApp bridge TypeScript
RUN cd bridge && npm run build

# ============================================
# Layer 4: Pre-build bridge to /opt (NOT inside ~/.vikingbot)
# The entrypoint will copy these to the mounted volume on first start
# ============================================
RUN mkdir -p /opt/vikingbot-bridge && \
    cp -r /app/bridge/. /opt/vikingbot-bridge/ && \
    cd /opt/vikingbot-bridge && \
    npm install --omit=dev

# Create placeholder ~/.vikingbot (will be overridden by host volume mount)
RUN mkdir -p /root/.vikingbot/workspace /root/.vikingbot/sessions /root/.vikingbot/sandboxes /root/.vikingbot/bridge

# Entrypoint: initializes bridge on first run when volume is mounted
COPY deploy/docker/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 18791

ENV PYTHONUNBUFFERED=1
ENV NODE_ENV=production
ENV OPENVIKING_CONFIG_FILE=/root/.vikingbot/ov.conf
WORKDIR /app

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["gateway"]
