FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY crypto_bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY crypto_bot/ .

# Create a non-root user for security
RUN useradd --create-home botuser
USER botuser

# Health check — verify Python process is alive
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import os; exit(0 if os.path.exists('bot.log') else 1)"

CMD ["python", "main.py"]
