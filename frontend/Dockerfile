# syntax=docker/dockerfile:1.7
# =============================================================================
# OPTIONAL - NOT USED IN THE CURRENT WORKFLOW AND NOT VERIFIED.
# Docker is not available on the development machine, so local development and
# all tests run against natively installed services instead (PostgreSQL Windows
# service on :5432, Memurai/Redis on :6379). See README.md. This file is kept only
# as a starting point should containers be adopted later; it has never been built.
# =============================================================================
# -----------------------------------------------------------------------------
# Frontend image: Next.js standalone server on Node 24 (Alpine).
# NEXT_PUBLIC_* values are inlined at build time, so they are build args.
# -----------------------------------------------------------------------------

FROM node:24-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund


FROM node:24-alpine AS builder
WORKDIR /app
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL} \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build


FROM node:24-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# Application files stay root-owned (read-only for the runtime user).
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

# Built-in unprivileged "node" user (uid/gid 1000).
USER node
EXPOSE 3000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:3000/').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"]

CMD ["node", "server.js"]
