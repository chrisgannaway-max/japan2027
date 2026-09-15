FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORTAL_DATA=/data PORTAL_CONFIG=/app/config/properties.yaml PORTAL_USERS=/app/config/users.yaml
VOLUME ["/data"]
EXPOSE 8000
# Render and most hosts hand the port in $PORT; 8000 keeps a plain `docker run` unchanged.
CMD ["sh", "-c", "uvicorn portal.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
