# JobFit Agent frontend（Phase 5/6, Next.js App Router）。两阶段：build -> runtime。
FROM node:24-alpine AS builder
WORKDIR /app

# registry 可覆盖：默认 npmmirror（本仓库开发环境网络下最稳），
# 其它环境可 `--build-arg NPM_REGISTRY=https://registry.npmjs.org`。
ARG NPM_REGISTRY=https://registry.npmmirror.com

COPY package.json package-lock.json .npmrc ./
RUN npm config set registry "$NPM_REGISTRY" && npm ci --no-audit --no-fund

COPY . .

# NEXT_PUBLIC_* 在 build 期内联，必须作为 build-arg 注入
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
RUN npm run build

FROM node:24-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production

COPY package.json package-lock.json ./
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/next.config.ts ./

EXPOSE 3000
CMD ["npm", "run", "start", "--", "--port", "3000", "--hostname", "0.0.0.0"]
