# Basic Python image – adjust version/base as you like
FROM python:3.11-slim

# Install journalctl (from systemd) so we can read the host journal
# You may want to trim this further in a real image.
RUN apt-get update && \
    apt-get install -y --no-install-recommends systemd && \
    rm -rf /var/lib/apt/lists/*

# Python deps
RUN pip install --no-cache-dir prometheus_client

WORKDIR /app

# Copy exporter code
COPY zpa_exporter.py /app/zpa_exporter.py

# Expose Prometheus scrape port
EXPOSE 8080

# Environment overrides (optional)
# ENV ZPA_SYSLOG_IDENTIFIER=zpa-connector-child
# ENV EXPORTER_PORT=8080

CMD ["python", "/app/zpa_exporter.py"]
