# ===========================
# BUILDER STAGE
# ===========================
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip tools
RUN pip install --upgrade pip setuptools wheel

# Copy requirements
COPY requirements.txt .

# Build wheels for dependencies
RUN pip wheel --no-cache-dir --wheel-dir /app/wheels -r requirements.txt


# ===========================
# FINAL STAGE
# ===========================
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/appuser

WORKDIR /app

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

# Copy wheels and requirements
COPY --from=builder /app/wheels /wheels
COPY --from=builder /app/requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir /wheels/*

# Copy project
COPY . /app

# Copy entrypoint
COPY entrypoint.sh /app/entrypoint.sh
RUN sed -i 's/\r$//g' /app/entrypoint.sh && chmod +x /app/entrypoint.sh

# Create static/media folders
RUN mkdir -p /app/staticfiles /app/media \
    && chown -R appuser:appuser /app

# Switch user
USER appuser

# Expose port
EXPOSE 8000

# Start script
ENTRYPOINT ["/app/entrypoint.sh"]

# Default command
CMD ["gunicorn", "work_pulse_be.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]