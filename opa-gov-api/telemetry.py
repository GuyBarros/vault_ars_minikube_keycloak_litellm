import os

from opentelemetry import metrics
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

_INSTRUMENTATION_NAME = "opa-gov-api"
_SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "opa-gov-api")
_SERVICE_VERSION = os.getenv("OTEL_SERVICE_VERSION", "0.1.0")


def _build_meter():
    reader = PrometheusMetricReader()
    resource = Resource.create(
        {"service.name": _SERVICE_NAME, "service.version": _SERVICE_VERSION}
    )
    provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(provider)
    return metrics.get_meter(_INSTRUMENTATION_NAME)


_meter = _build_meter()

VIOLATIONS_COUNTER = _meter.create_counter(
    name="opa_violations",
    description="Count of /evaluate calls blocked by OPA (prompt_injection and/or unsafe_code).",
    unit="1",
)
PII_MASKING_SUCCESSFUL_COUNTER = _meter.create_counter(
    name="opa_pii_masking_successful",
    description="Count of /mask calls where OPA successfully detected and masked PII (output differs from input).",
    unit="1",
)


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
