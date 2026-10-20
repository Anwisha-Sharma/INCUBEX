# RescueMesh Standalone Node.js Local Hub Dockerfile
# Module E7: Lightweight relay container for hotspots and emergency laptops

FROM node:22-alpine

WORKDIR /app

COPY package.json ./

# Install ws dependency
RUN npm install --omit=dev

COPY server.js ./

USER node

ENV PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
    CMD wget -qO- http://localhost:8080/health || exit 1

CMD ["node", "server.js"]
