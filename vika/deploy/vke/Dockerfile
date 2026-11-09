FROM node:24.21.0-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates python3 ffmpeg tini \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/vika
COPY package.json package-lock.json ./
COPY packages/daemon/package.json packages/daemon/package.json
COPY packages/web/package.json packages/web/package.json
COPY packages/updater/package.json packages/updater/package.json
COPY packages/cli/package.json packages/cli/package.json
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY . .
RUN node scripts/install-lark-cli.mjs && node scripts/install-slack-cli.mjs \
    && mkdir -p /data/vika /data/workspace /data/lark-cli && chown -R node:node /data /opt/vika
USER node
ENV VIKA_CONFIG=/config/config.json
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["node", "packages/daemon/src/main.mjs"]
