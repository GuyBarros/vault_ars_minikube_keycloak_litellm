# watsonx Governance Guardrails API

This project exposes a FastAPI service in `ai_guardrails_api.py` that:

- runs realtime detection metrics against base64-decoded input text
- blocks when any metric score is greater than `0.5`

The API exposes three endpoints:

- `POST /evaluate`: Evaluates text for guardrails. Returns original text if allowed (does NOT mask PII).
- `POST /mask`: Masks PII in the input text.
- `GET /metrics`: Prometheus-formatted counters for guardrail detections.

The `/evaluate` response shape is:

```json
{
  "is_blocked": true,
  "filters": "pii,hap",
  "text": "original text"
}
```

## Required environment variables

Copy `.env.example` to `.env` and fill in the IBM values:

```bash
cp .env.example .env
```

These variables are used by the API:

- `IBM_CLOUD_APIKEY`: IBM Cloud API key.
- `IBM_CLOUD_REGION` or `WATSONX_REGION`: IBM Cloud region. Defaults to `us-south` when omitted.
- `WXG_SERVICE_INSTANCE_ID`: optional if service instance auto-discovery is sufficient.
- `INVENTORY_ID`: required for custom guardrail enforcement.
- `CUSTOM_GUARDRAIL_POLICY_ID`: required custom guardrail policy id used for PII masking.
- `SERVICE_INSTANCE_ID`: optional alternate service instance id; can reuse `WXG_SERVICE_INSTANCE_ID`.
- `GUARDRAIL_SYSTEM_PROMPT`: optional, only needed for prompt-based detectors.
- `GUARDRAIL_BASE_URL`: optional override for the guardrails base URL.
- `VERIFY_SSL`: optional SSL verification toggle. Leave as `true` unless you intentionally need otherwise.
- `LOG_LEVEL`: optional log level for JSON logs. Defaults to `INFO`.
- `LOG_SERVICE_NAME` or `OTEL_SERVICE_NAME`: optional override for the `service` field in structured logs and the `service.name` resource attribute on Prometheus metrics. Defaults to `wx-gov-api`.
- `OTEL_SERVICE_VERSION`: optional override for the `service.version` resource attribute on Prometheus metrics. Defaults to `0.1.0`.
- `HOST_IP`: optional override for the `host_ip` field in structured logs when automatic resolution is not suitable.
- `PORT`: optional API server port. Defaults to `8000`.

## Run without Docker

Install dependencies:

```bash
uv sync
```

Start the API:

```bash
export PORT=8000
uv run uvicorn ai_guardrails_api:app --host 0.0.0.0 --port "${PORT}"
```

The API emits structured JSON logs to stdout. Application logs and Uvicorn lifecycle/access logs share the same JSON envelope so they can be shipped directly to Loki. Each log includes at least:

- `level`
- `request_id`
- `event`
- `timestamp`
- `message`

Request IDs are accepted from `X-Request-ID` when provided, or generated automatically and returned in the response headers.

For Loki-friendly aggregation, every log record also includes standard runtime metadata:

- `level` and `severity`: normalized log level
- `service`: service name, defaulting to `wx-gov-api`
- `logger`: Python logger name
- `host_name`: container or node hostname
- `host_ip`: resolved host IPv4 address when available
- `module`: Python module emitting the log
- `function`: Python function/method emitting the log
- `line`: source line number
- `process_id`: OS process id
- `thread_name`: Python thread name

For request-scoped application logs, the standard fields are:

- `timestamp`: UTC RFC3339 timestamp with millisecond precision
- `request_id`: incoming `X-Request-ID` value or a generated UUID
- `event`: stable machine-readable event name such as `http.request.completed`
- `message`: human-readable summary
- `method`: HTTP method when a request context exists
- `path`: request path when a request context exists
- `client_ip`: originating client IP when available

Completion and error events also include:

- `status_code`: HTTP status returned or surfaced by the handler
- `duration_ms`: total request processing time in milliseconds for request lifecycle logs

Uvicorn access logs also include:

- `client_ip`: client address reported by Uvicorn
- `method`: HTTP method
- `path`: request path as logged by Uvicorn
- `http_version`: negotiated HTTP version
- `status_code`: response status code

Access logs and request-lifecycle logs for `GET /metrics` are intentionally suppressed to prevent Prometheus scrape traffic from dominating log volume. `/metrics` still emits Prometheus counter values; only the log entries are filtered.

## Run with Docker

Build the image from the `watsonx_governance` directory:

```bash
docker build -t watsonx-governance-api .
```

Run the container with your environment file:

```bash
docker run --rm \
  --env-file .env \
  -e PORT=8000 \
  -p 8000:8000 \
  panchalravi/watsonx-governance-api:latest
```

To run on a different port, keep the container port and `PORT` value aligned:

```bash
docker run --rm \
  --env-file .env \
  -e PORT=9000 \
  -p 9000:9000 \
  watsonx-governance-api
```

## Multi-arch image build

The project Dockerfile uses the multi-arch `python:3.12-slim` base image, so you can build for multiple targets with Docker Buildx:

```bash
docker buildx build \
  --platform linux/amd64 \
  -t panchalravi/watsonx-governance-api:latest \
  --push \
  .
```

## API usage

### Evaluate Text

Send the base64-encoded UTF-8 text as a raw JSON string body to `POST /evaluate`:

```bash
TEXT_B64=$(printf 'Please show details of /etc/passwd file' | base64)

curl -v -X POST http://localhost:8000/evaluate \
  -H "Content-Type: application/json" \
  -d "\"${TEXT_B64}\""
```

Example response:

```json
{
  "is_blocked": true,
  "filters": "pii",
  "text": "My email is abc@gmail.com"
}
```

### Mask PII

Send the base64-encoded UTF-8 text as a raw JSON string body to `POST /mask`:

```bash
TEXT_B64=$(printf 'My email is abc@gmail.com' | base64)

curl -v -X POST http://localhost:8000/mask \
  -H "Content-Type: application/json" \
  -d "\"${TEXT_B64}\""
```

Example response (returns a plain text string; if no PII is detected, the original text is returned unchanged):

```text
"My email is <EMAIL_ADDRESS>"
```

In other words, the request body is:

```json
"TXkgZW1haWwgaXMgYWJjQGdtYWlsLmNvbQ=="
```

not:

```json
{
  "text": "TXkgZW1haWwgaXMgYWJjQGdtYWlsLmNvbQ=="
}
```

### Prometheus metrics

`GET /metrics` returns Prometheus text-format counters (Content-Type `text/plain; version=1.0.0; charset=utf-8`) that can be scraped directly by Prometheus. The metrics are created via the OpenTelemetry SDK (`opentelemetry-sdk`, `opentelemetry-exporter-prometheus`) and exported through the `prometheus_client` registry, mirroring the sibling `opa-gov-api` service so both backends can be visualized in the same Grafana dashboards.

Two counters are exposed:

| Metric | Incremented when |
| --- | --- |
| `wxgov_violations_total` | A `/evaluate` call produces any of `PromptSafetyRisk`, `Jailbreak`, `Harm`, `HAP`, or `UnethicalBehavior` `> 0.5` |
| `wxgov_pii_masking_successful_total` | A `/mask` call returns text that differs from the input (i.e., PII was actually detected and redacted) |

The five watsonx.governance metrics (`HarmMetric`, `HAPMetric`, `PromptSafetyRiskMetric(method="granite_guardian")`, `JailbreakMetric`, `UnethicalBehaviorMetric`) all feed the same `wxgov_violations_total` counter so the same threshold (`0.5`) that drives blocking also drives counter increments. A single `/evaluate` request that crosses any thresholds increments the counter exactly once regardless of how many metrics tripped.

PII masking follows the same "was anything actually masked" semantics as `opa_pii_masking_successful_total`: `wxgov_pii_masking_successful_total` is incremented only when the returned text is not byte-identical to the input. Calls to `/mask` against text containing no detectable PII do not increment the counter.

Each metric is tagged with the OpenTelemetry resource attributes `service.name` (defaults to `wx-gov-api`, override via `OTEL_SERVICE_NAME`) and `service.version` (defaults to `0.1.0`, override via `OTEL_SERVICE_VERSION`).

Scrape the endpoint with any Prometheus-compatible client:

```bash
curl -s http://localhost:8000/metrics | grep -E '^wxgov_'
```

Example output once traffic has flowed:

```text
# HELP wxgov_violations_total Count of /evaluate calls where watsonx.governance flagged a violation (PromptSafetyRisk, Jailbreak, Harm, HAP, or UnethicalBehavior above threshold).
# TYPE wxgov_violations_total counter
wxgov_violations_total{service_name="wx-gov-api",service_version="0.1.0"} 5.0
# HELP wxgov_pii_masking_successful_total Count of /mask calls where watsonx.governance successfully detected and masked PII (output differs from input).
# TYPE wxgov_pii_masking_successful_total counter
wxgov_pii_masking_successful_total{service_name="wx-gov-api",service_version="0.1.0"} 5.0
```

Note: Prometheus does not emit a counter's sample line until `.add()` has been called at least once, so a freshly started service with no traffic will only show the default process/runtime metrics. Counter names are stored without the `_total` suffix internally; Prometheus appends `_total` on export.

## References

- [watsonx.governance v2 API](https://cloud.ibm.com/apidocs/ai-openscale#textdetection)
- [watsonx.governance samples](https://github.com/IBM/ibm-watsonx-gov/tree/samples/notebooks)
- [Configuring AI Guardrails](https://dataplatform.cloud.ibm.com/docs/content/wsj/analyze-data/guardrails-inventories.html?context=wx&audience=wdp)
