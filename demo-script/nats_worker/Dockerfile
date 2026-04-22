FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY worker.py /app/nats_worker/worker.py
# Make it importable as a package
RUN touch /app/nats_worker/__init__.py

EXPOSE 9100

CMD ["python", "-m", "nats_worker.worker"]
