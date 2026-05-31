# syntax=docker/dockerfile:1.7
# Multi-stage image for OpenViking Web Studio in auto-proxy mode.
# Stage 1 builds the static SPA. Stage 2 ships only the runtime proxy +
# pre-built dist/, on node:22-alpine for a small footprint (~150 MB).

ARG NODE_VERSION=22

FROM node:${NODE_VERSION}-alpine AS build
WORKDIR /app
# Copy lockfile + manifest first so npm cache layers stay reusable.
COPY package.json package-lock.json* pnpm-lock.yaml* ./
# pnpm-lock.yaml exists in the repo but the publish flow uses npm; prefer npm ci
# for deterministic installs, fall back to npm install if the lockfile is stale.
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY . .
RUN npm run build

FROM node:${NODE_VERSION}-alpine AS runtime
ENV NODE_ENV=production \
    OV_STUDIO_HOST=0.0.0.0
WORKDIR /app
COPY --from=build /app/server ./server
COPY --from=build /app/dist ./dist
COPY --from=build /app/package.json ./package.json
# proxy.mjs is zero-dep — no production node_modules needed.
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- "http://127.0.0.1:${PORT:-3000}/_studio/runtime-config.json" >/dev/null 2>&1 || exit 1
CMD ["node", "server/proxy.mjs"]
