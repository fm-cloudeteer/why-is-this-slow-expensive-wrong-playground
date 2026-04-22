FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    fastapi \
    uvicorn[standard] \
    httpx \
    langfuse \
    pydantic \
    nats-py \
    prometheus_client \
    opentelemetry-api \
    opentelemetry-sdk \
    opentelemetry-exporter-otlp-proto-grpc

COPY demo_app/ /app/demo_app/

EXPOSE 8080

CMD ["uvicorn", "demo_app.main:app", "--host", "0.0.0.0", "--port", "8080"]
