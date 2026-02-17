FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Install Node.js 20
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates gnupg git && \
    mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" > /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get purge -y gnupg && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy all project files
COPY pyproject.toml README.md ./
COPY license/LICENSE ./LICENSE
COPY vikingbot/ vikingbot/
COPY bridge/ bridge/

# Build WhatsApp bridge
WORKDIR /app/bridge
RUN npm install && npm run build
WORKDIR /app

# Install Python dependencies
RUN uv pip install --system --no-cache .

# Install global npm packages for sandbox
RUN npm install -g @anthropic-ai/sandbox-runtime

# Create necessary directories
RUN mkdir -p /root/.vikingbot /root/.vikingbot/workspace /root/.vikingbot/sandboxes /root/.vikingbot/bridge

# Pre-build bridge to correct location
RUN cp -r /app/bridge/* /root/.vikingbot/bridge/ && \
    cd /root/.vikingbot/bridge && \
    npm install --omit=dev

# Gateway default port
EXPOSE 18790

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV NODE_ENV=production

# Set working directory
WORKDIR /app

ENTRYPOINT ["vikingbot"]
CMD ["status"]
