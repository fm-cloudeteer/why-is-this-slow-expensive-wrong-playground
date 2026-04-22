"""
Demo configuration — loaded from environment variables.
Copy .env.example to .env and fill in your values.
"""

import os
from dataclasses import dataclass, field
from typing import Dict


@dataclass
class TenantConfig:
    tenant_id: str
    api_key: str
    model: str = "cyankiwi/Qwen3.5-9B-AWQ-4bit"
    rps: float = 1.0                 # requests per second at baseline


@dataclass
class DemoConfig:
    # Stack endpoints
    gateway_url: str                 # Gravitee APIM proxy URL
    langfuse_url: str
    langfuse_public_key: str
    langfuse_secret_key: str
    prometheus_url: str
    tempo_url: str
    feature_flag_url: str            # internal feature flag service

    # Tenants
    tenants: Dict[str, TenantConfig] = field(default_factory=dict)

    # Load parameters
    baseline_rps_total: float = 4.0  # across all tenants (1 per tenant)
    burst_rps_total: float = 30.0    # burst multiplier target (~7.5x overload)
    burst_duration_seconds: int = 90

    # Timing
    baseline_duration_seconds: int = 180
    wrong_duration_seconds: int = 240
    expensive_duration_seconds: int = 180
    slow_duration_seconds: int = 180      # total: burst_duration_seconds + drain
    recovery_duration_seconds: int = 120

    # Quality scoring thresholds
    quality_alert_threshold: float = 0.65

    @classmethod
    def from_env(cls) -> "DemoConfig":
        tenants = {
            "tenant_a": TenantConfig(
                tenant_id="tenant_a",
                api_key=os.environ["TENANT_A_API_KEY"],
                rps=1.0,
            ),
            "tenant_b": TenantConfig(
                tenant_id="tenant_b",
                api_key=os.environ["TENANT_B_API_KEY"],
                rps=1.0,
            ),
            "tenant_c": TenantConfig(
                tenant_id="tenant_c",
                api_key=os.environ["TENANT_C_API_KEY"],
                rps=1.0,
            ),
            "tenant_d": TenantConfig(
                tenant_id="tenant_d",
                api_key=os.environ["TENANT_D_API_KEY"],
                rps=1.0,
            ),
        }
        return cls(
            gateway_url=os.environ.get("GATEWAY_URL", "http://localhost:8082"),
            langfuse_url=os.environ.get("LANGFUSE_URL", "http://localhost:3000"),
            langfuse_public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
            langfuse_secret_key=os.environ["LANGFUSE_SECRET_KEY"],
            prometheus_url=os.environ.get("PROMETHEUS_URL", "http://localhost:9090"),
            tempo_url=os.environ.get("TEMPO_URL", "http://localhost:3200"),
            feature_flag_url=os.environ.get("FEATURE_FLAG_URL", "http://localhost:8080"),
            tenants=tenants,
            burst_rps_total=float(os.environ.get("BURST_RPS", "30")),
        )
