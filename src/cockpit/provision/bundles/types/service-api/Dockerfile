# Dockerfile — stub de service semé par le cockpit (P3). Zéro dépendance réseau au-delà de l'image de
# base : l'app d'amorçage tourne sur la stdlib Python et répond 200. Remplace `app.py` par ton vrai
# service (FastAPI, etc.) au fil des features, en gardant l'écoute sur 0.0.0.0:8000 (contrat compose).
FROM python:3.12-slim
WORKDIR /app
COPY . .
EXPOSE 8000
CMD ["python", "app.py"]
