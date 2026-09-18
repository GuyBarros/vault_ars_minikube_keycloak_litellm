# Copilot Instructions for `watsonx_governance`

## Build, run, and validation commands

- Install dependencies (local, per README):  
  `uv sync`
- Run API locally:  
  `export PORT=8000 && uv run uvicorn ai_guardrails_api:app --host 0.0.0.0 --port "${PORT}"`
- Docker build (amd64 only):  
  `docker build -t watsonx-governance-api .`
- Docker run:  
  `docker run --rm --env-file .env -e PORT=8000 -p 8000:8000 watsonx-governance-api`
- Kubernetes deploy (existing manifest):  
  `kubectl apply -f k8s/deployment.yaml`

Automated lint/test suites are not configured in this repository yet (no pytest/ruff/mypy/tox config found).  
Script-level checks can be run directly:

- Realtime metrics script:  
  `python realtime_detections.py --text "My email is abc@gmail.com"`
- Realtime detection REST API script:  
  `python realtime_detections_api.py --text "My email is abc@gmail.com"`
- PII masking policy enforcement script:  
  `python pii_masking.py enforce --text "My email is abc@gmail.com"`

## High-level architecture

The project exposes a single FastAPI endpoint in `ai_guardrails_api.py`:

- `POST /evaluate` accepts base64-encoded UTF-8 text.
- It decodes input, evaluates watsonx governance metrics via `realtime_detections.evaluate_text_metrics`, and applies threshold logic (`THRESHOLD = 0.5`).
- If `pii` exceeds threshold, it masks text via `pii_masking.mask_pii_text`.
- If any triggered filter other than `pii` is present, it returns HTTP 400 with `BLOCKED_CONTENT_MESSAGE` (or default blocked text).
- Otherwise, it returns masked/original text directly.

Supporting modules:

- `realtime_detections.py`: SDK-based metric evaluation using `ibm-watsonx-gov` `MetricsEvaluator` with PII, HAP, prompt safety risk, and jailbreak metrics. Also auto-resolves `WXG_SERVICE_INSTANCE_ID` via IBM Resource Controller when absent.
- `pii_masking.py`: guardrails policy enforcement client for masking (`/guardrails-manager/v1/enforce/{policy_id}`), with strict extraction from `entity.text`.
- `realtime_detections_api.py`: alternative direct REST client for text detection (`/ml/v1/text/detection`) with detector JSON override support.

Deployment assets:

- `Dockerfile` builds/runs `ai_guardrails_api` and hard-fails non-`amd64` targets.
- `k8s/deployment.yaml` deploys the container, loads env from `watsonx-governance-env` Secret, and uses `/openapi.json` probes.

## Key repository conventions

- Environment variable aliases are intentionally supported; keep compatibility when adding config:
  - Region: `WATSONX_REGION` or `IBM_CLOUD_REGION` (default `us-south`)
  - Service instance: `WXG_SERVICE_INSTANCE_ID` or `SERVICE_INSTANCE_ID`
  - Policy ID: `CUSTOM_GUARDRAIL_POLICY_ID` or `POLICY_ID` (in `pii_masking.py`)
- `VERIFY_SSL` is parsed via string booleans (`0/false/no/off` => false); preserve this behavior in new network code.
- Fail fast on config/API issues with explicit `RuntimeError` messages; callers map these to HTTP errors.
- API input contract is base64 text, not raw text; keep `decode_base64_text(..., validate=True)` behavior.
- Current blocking policy: `pii` alone masks but does **not** block; non-PII triggered filters drive blocking.
- Detector sets in masking flow are intentionally constrained (`pii` active; others commented). Treat changes as behavioral and coordinate with policy owners.
