# Dockerfile — stub de front semé par le forgemaster (P3). Zéro dépendance réseau au-delà de l'image de
# base : la page d'amorçage tourne sur le module http natif de Node et répond 200. Remplace `server.mjs`
# par ton vrai front (build Vite → serveur statique) au fil des features, en gardant 0.0.0.0:8000.
FROM node:22-alpine
WORKDIR /app
COPY . .
EXPOSE 8000
CMD ["node", "server.mjs"]
