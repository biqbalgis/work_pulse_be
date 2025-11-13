# ===========================
# BASE PYTHON IMAGE
# ===========================
FROM python:3.12-slim

# Prevent Python from buffering output
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt /app/

# Install Python dependencies
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Copy project into container
COPY . /app/

# Collect static files (optional for production)
RUN python manage.py collectstatic --noinput

# Expose Django app port (Gunicorn)
EXPOSE 8000

# Start Gunicorn server
CMD ["gunicorn", "work_pulse_be.wsgi:application", "--bind", "0.0.0.0:8000"]
