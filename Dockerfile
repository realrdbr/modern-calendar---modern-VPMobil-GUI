# Multi-stage Dockerfile for cal11 Jahrgangskalender
FROM node:22-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .
RUN npm run build

# Production Runner
FROM node:22-alpine AS runner

WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000

COPY package*.json ./
RUN npm install --omit=dev

COPY --from=builder /app/dist ./dist
COPY --from=builder /app/public ./public
COPY --from=builder /app/icons ./icons
COPY --from=builder /app/server ./server

EXPOSE 3000

CMD ["node", "dist/server.cjs"]
