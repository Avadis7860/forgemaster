# Dockerfile — build statique d'un site vitrine Astro (SSG) servi par nginx. Semé par le cockpit (P3).
# Étape 1 : Node build l'app Astro (web/) → SSG statique (web/dist). Étape 2 : nginx sert le statique sur
# 0.0.0.0:8000 (aucun runtime applicatif). Garde 0.0.0.0:8000 : c'est le port interne du contrat de run.
FROM node:22-alpine AS build
WORKDIR /app/web
COPY web/package.json web/package-lock.json* ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/web/dist /usr/share/nginx/html
EXPOSE 8000
CMD ["nginx", "-g", "daemon off;"]
