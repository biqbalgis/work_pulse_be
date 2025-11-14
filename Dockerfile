# ===========================
# BASE PYTHON IMAGE
# ===========================
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (layer caching)
COPY requirements.txt /app/

RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Create static directory (prevents collectstatic crash)
RUN mkdir -p /app/staticfiles

# Copy project
COPY . /app/

# Collect static files (production safe)
RUN python manage.py collectstatic --noinput

EXPOSE 8000

# Final CMD — Gunicorn only (migrations handled by docker-compose)
CMD ["gunicorn", "work_pulse_be.wsgi:application",
     "--bind", "0.0.0.0:8000",
     "--workers", "2",
     "--timeout", "120",
     "--log-level", "debug",
     "--error-logfile", "-",
     "--access-logfile", "-"]

