# Dockerfile — build statique d'un site vitrine Astro (SSG) servi par nginx. Semé par le cockpit (P3).
# Étape 1 : Node build l'app Astro (web/) → SSG statique (web/dist). Étape 2 : nginx sert le statique sur
# 0.0.0.0:8000 (aucun runtime applicatif). Garde 0.0.0.0:8000 : c'est le port interne du contrat de run.
# Images **pleinement qualifiées** (`docker.io/library/…`) : un short-name (`node:22-alpine`) exige des
# `unqualified-search-registries` dans la `registries.conf` de l'hôte — absents sous podman rootful (build KO
# « short-name did not resolve »). La qualification rend le build portable, indépendant de la conf hôte.
FROM docker.io/library/node:22-alpine AS build
WORKDIR /app/web
COPY web/package.json web/package-lock.json* ./
# `npm ci` exige un lockfile ; le scaffold site-vitrine n'en sème pas (le glob `*` ci-dessus masque son
# absence). On calque la logique de référence `webbuild.py` : `npm ci` (reproductible) SI un lockfile est là,
# sinon `npm install`. Un `if/else` — pas un `npm ci || npm install` qui masquerait un échec `npm ci` légitime
# (lockfile désynchro).
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY web/ ./
RUN npm run build

FROM docker.io/library/nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/web/dist /usr/share/nginx/html
EXPOSE 8000
CMD ["nginx", "-g", "daemon off;"]
