# ===========================
# BUILDER STAGE
# ===========================
FROM python:3.12-slim AS builder

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /app/wheels -r requirements.txt

# ===========================
# FINAL STAGE
# ===========================
FROM python:3.12-slim

# Create a non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/appuser

WORKDIR /app

# Install runtime dependencies (libpq for postgres)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

# Copy wheels from builder
COPY --from=builder /app/wheels /wheels
COPY --from=builder /app/requirements.txt .

# Install dependencies
RUN pip install --no-cache /wheels/*

# Copy project files first
COPY . /app/

# Copy and set up entrypoint script (do this AFTER copying project files to ensure it's fresh)
COPY entrypoint.sh /app/entrypoint.sh
RUN sed -i 's/\r$//g' /app/entrypoint.sh && chmod +x /app/entrypoint.sh

# Create directory for static and media files and give ownership to appuser
RUN mkdir -p /app/staticfiles /app/media && chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Collect static files (using a dummy secret key for build process)
# Note: We do this here so the image is ready to serve static files
RUN SECRET_KEY=django-insecure-c25blbdkg7f^+=u-8xejk54zwhetjxehk0i1=krxio1q$3hd*& python manage.py collectstatic --noinput

# Expose port
EXPOSE 8000

# Set entrypoint
ENTRYPOINT ["/app/entrypoint.sh"]

# Default command
CMD ["gunicorn", "work_pulse_be.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
