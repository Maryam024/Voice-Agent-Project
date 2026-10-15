# Stage 1: build the React app
FROM node:20-slim AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Django + the built React app, served by one process
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app/backend
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
COPY --from=web /web/dist ./frontend_dist
RUN SECRET_KEY=build-only DEBUG=0 python manage.py collectstatic --noinput

ENV DEBUG=0 STT_ENGINE=groq TTS_ENGINE=browser

# migrate, add demo doctors only if the database is empty, then start the ASGI server (needed for Socket.IO)
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py seed_demo --if-empty && uvicorn config.asgi:application --host 0.0.0.0 --port ${PORT:-10000} --proxy-headers --forwarded-allow-ips '*'"]
