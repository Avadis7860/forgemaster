# Dockerfile — image de run semée par le forgemaster (deploy chain). Univers TypeScript unifié : on build le
# front (Vite → dist/) et on sert le statique + l'API Hono derrière un unique port 8000 (cf. server/prod.ts).
# Générique (aucun nom de projet en dur) : le jeu réel remplit web/ + server/ au fil des features, le contrat
# mono-port 0.0.0.0:8000 reste (le forgemaster publie ${FORGEMASTER_PORT} → 8000).
FROM node:22-alpine
WORKDIR /app
COPY . .
RUN npm ci && npm run build
EXPOSE 8000
CMD ["npx", "tsx", "server/prod.ts"]
