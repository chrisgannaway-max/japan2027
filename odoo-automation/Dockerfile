FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORTAL_DATA=/data PORTAL_CONFIG=/app/config/properties.yaml PORTAL_USERS=/app/config/users.yaml
VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "portal.app:app", "--host", "0.0.0.0", "--port", "8000"]
