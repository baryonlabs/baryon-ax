FROM node:20-bookworm-slim

ARG KORDOC_VERSION=^4.24.0
ENV NODE_ENV=production \
    KORDOC_OFFLINE=1 \
    KORDOC_VERSION=${KORDOC_VERSION} \
    PORT=8080

WORKDIR /app
COPY package.json ./
RUN npm install --omit=dev --no-audit --no-fund \
    && npm cache clean --force

COPY server.mjs ./
USER node
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:8080/health').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"

CMD ["node", "server.mjs"]
