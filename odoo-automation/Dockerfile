FROM python:3.12-slim
WORKDIR /app

# Reading a *scanned* vendor invoice needs Tesseract, and that side is off: Champion use Odoo's
# own bill digitization.  Carrying an OCR engine and its language data in every image, and
# patching them, for something that never runs is a poor trade -- so it is opt-in.  Build with
# --build-arg INVOICE_OCR=1 to get it; nothing else changes either way, because the reader asks
# ocr_available() before using it and says so in the review notes when it is not there.
ARG INVOICE_OCR=0

COPY requirements.txt requirements-invoices.txt ./
RUN if [ "$INVOICE_OCR" = "1" ]; then \
        apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
        && rm -rf /var/lib/apt/lists/*; \
    fi
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$INVOICE_OCR" = "1" ]; then pip install --no-cache-dir -r requirements-invoices.txt; fi
COPY . .
ENV PORTAL_DATA=/data PORTAL_CONFIG=/app/config/properties.yaml PORTAL_USERS=/app/config/users.yaml
VOLUME ["/data"]
EXPOSE 8000
# Render and most hosts hand the port in $PORT; 8000 keeps a plain `docker run` unchanged.
CMD ["sh", "-c", "uvicorn portal.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
