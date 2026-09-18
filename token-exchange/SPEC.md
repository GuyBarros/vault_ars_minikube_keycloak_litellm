---

# Specification: Identity Broker API (Python)

## 1. Overview

### 1.1 Purpose

Build a **Python-based identity broker** that:

1. Implements an **on-behalf-of (OBO) token exchange** with **IBM Verify**.
2. Uses **in-memory caching** to reuse valid exchanged access tokens.
3. Exposes the functionality via a **high-performance API service** for external consumers.
4. Implements production-grade engineering practices:

   * security
   * logging
   * resilience
   * observability

The service acts as an **identity brokering layer** for internal services.

The system implements an **on-behalf-of (OBO) token exchange flow** with **IBM Verify**:

5. Accepts a `subject_token` (caller's access token) and an `actor_token`.
6. Performs an RFC 8693 Token Exchange against IBM Verify to obtain a new access token on behalf of the subject.
7. Caches the resulting OBO access token with JWT-expiry-aware validation.

---

# 2. System Architecture

## 2.1 High-Level Architecture

```
Client Application
        │
        │ REST API
        ▼
Identity Broker API
(FastAPI Service)
        │
        └── POST /v1/identity/obo-token ──► OBOBroker Library ──────────► IBM Verify
```

Components:

1. **Identity Broker API**
2. **OBO Broker Library**
3. **In-memory cache**
4. **IBM Verify integration**

---

# 3. Technology Stack

## 3.1 Core Libraries

| Concern            | Library    |
| ------------------ | ---------- |
| API Framework      | FastAPI    |
| ASGI Server        | Uvicorn    |
| HTTP client        | requests   |
| Caching            | cachetools |
| JWT parsing        | PyJWT      |
| Retry handling     | tenacity   |
| Logging            | structlog  |
| Validation         | pydantic   |

## 3.2 Dependency Management

Dependency manager: **uv**

* Dependencies declared in `pyproject.toml` under `[project].dependencies`
* Dev dependencies (pytest, httpx) declared under `[dependency-groups].dev`
* Lockfile: `uv.lock` (committed to version control)
* Install: `uv sync`
* Run commands: `uv run <command>` (e.g. `uv run pytest`, `uv run uvicorn api.main:app`)

Recommended API framework:

* **FastAPI**

Chosen because it provides:

* High performance (ASGI)
* Native async support
* Automatic OpenAPI generation
* Strong request validation

---

# 4. Functional Requirements

## 4.1 On-Behalf-Of (OBO) Token Exchange

The system must:

1. Accept a `subject_token` (caller's access token, JWT) and an `actor_token`.
2. Perform an RFC 8693 token exchange against the IBM Verify token endpoint.
3. Return the IBM Verify **access token** issued on behalf of the subject.

IBM Verify token endpoint:

```
POST <VERIFY_BASE_URL>/oauth2/token
```

Reference curl:

```bash
curl -s -X POST https://tenant.verify.ibm.com/oauth2/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=$OBO_CLIENT_ID" \
  -d "client_secret=$OBO_CLIENT_SECRET" \
  -d "grant_type=urn:ietf:params:oauth:grant-type:token-exchange" \
  -d "requested_token_type=urn:ietf:params:oauth:token-type:access_token" \
  -d "subject_token_type=urn:ietf:params:oauth:token-type:access_token" \
  -d "subject_token=$access_token" \
  -d "actor_token_type=<actor-token-type>" \
  -d "actor_token=$actor_token"
```

---

# 5. API Specification

## 5.1 Endpoint

```
POST /v1/identity/obo-token
```

---

## 5.2 Request Body

```json
{
  "subject_token": "<redacted-subject-token>",
  "actor_token": "<redacted-actor-token>"
}
```

### Fields

| Field         | Type   | Description                                              |
| ------------- | ------ | -------------------------------------------------------- |
| subject_token | string | Caller's access token (JWT) — the identity to act on behalf of |
| actor_token   | string | Token identifying the actor performing the exchange |

---

## 5.3 Response

```json
{
  "access_token": "<redacted-access-token>",
  "cached": false
}
```

### Fields

| Field        | Description                               |
| ------------ | ----------------------------------------- |
| access_token | IBM Verify access token issued for subject |
| cached       | Whether the token was served from cache   |

---

## 5.4 HTTP Status Codes

| Code | Description                         |
| ---- | ----------------------------------- |
| 200  | Success                             |
| 400  | Invalid request                     |
| 401  | IBM Verify authentication failure   |
| 500  | Token exchange error                |
| 503  | IBM Verify unreachable              |

---



# 6. OBO Library Specification

## 6.1 Public Method

```python
exchange_obo_token(
    subject_token: str,
    actor_token: str,
) -> OBOTokenResult
```

### Inputs

| Parameter     | Type |
| ------------- | ---- |
| subject_token | str  |
| actor_token   | str  |

### Output

Returns:

```
OBOTokenResult(access_token, cached)
```

---



## 7.1 Cache Type

* In-memory
* thread-safe
* TTL aware

Recommended implementation:

```
cachetools.TTLCache
```

---

## 7.2 Cache Key

Cache key for OBO tokens derived from:

```
subject_token + actor_token
```

Both are hashed to prevent token exposure.

Example:

```
sha256(subject_token + actor_token)
```

---

## 7.3 Cache Validation

Before returning cached token:

1. Decode JWT.
2. Extract `exp` claim.
3. Check expiration.

```
if current_time < exp - safety_buffer
    return cached token
else
    refresh token
```

---

## 7.4 Expiration Safety Buffer

```
SAFETY_BUFFER = 30 seconds
```

Prevents edge expiration failures.

---

# 8. API Layer Design

## 8.1 FastAPI Router

Example endpoint design:

```
POST /v1/identity/obo-token
```

Handler flow:

```
validate request
     │
     ▼
call OBOBroker
     │
     ▼
return exchanged access token
```

---

## 8.2 Request Model

Using **Pydantic**:

```python
class OBOTokenRequest(BaseModel):
    subject_token: str
    actor_token: str
```

---

## 8.3 Response Model

```python
class OBOTokenResponse(BaseModel):
    access_token: str
    cached: bool
```

---

# 9. Module Structure

```
.
│
├── api/
│   ├── main.py
│   └── routes.py
│
├── verify/
│   ├── __init__.py
│   ├── verify_client.py
│   └── obo_broker.py
│
├── models/
│   └── schemas.py
│
├── app_logging/
│   └── logger.py
│
├── exceptions/
│   └── errors.py
│
└── config/
    └── settings.py
```

Notes:

* Source packages live directly at the repository root; there is no extra top-level `identity_broker/` package directory.
* The logging package is named `app_logging/` to avoid colliding with Python's standard-library `logging` module.

---

# 10. Error Handling

## 10.1 Custom Exceptions

```
CacheError
VerifyOBOError
VerifyAuthenticationError
VerifyTokenExchangeError
```

---

## 10.2 API Error Mapping

| Exception                 | HTTP Response |
| ------------------------- | ------------- |
| CacheError                | 500           |
| VerifyAuthenticationError | 401           |
| VerifyTokenExchangeError  | 500           |

---

# 11. Retry Strategy

Use **tenacity**.

Retry when:

* 5xx errors

Policy:

```
max_attempts = 3
exponential backoff
```

---

# 12. Logging

Logging requirements:

* structured JSON logs
* correlation IDs
* no secrets

Example log:

```json
{
  "event": "verify_obo_token_exchange",
  "cache_hit": true,
  "duration_ms": 25
}
```

Never log:

* subject tokens
* actor tokens
* access tokens

---

# 13. Security Requirements

## 13.1 Transport Security

* HTTPS required

---

## 13.2 Sensitive Data Handling

Never log:

```
subject_token
actor_token
access_token
```

---

## 13.3 IBM Verify OBO Configuration

Environment variables required for the OBO token exchange:

| Variable                            | Description                                          | Required |
| ----------------------------------- | ---------------------------------------------------- | -------- |
| `IDENTITY_BROKER_VERIFY_BASE_URL`   | IBM Verify tenant base URL (no trailing `/`)                | Yes      |
| `IDENTITY_BROKER_OBO_CLIENT_ID`     | Client ID for the OBO token exchange (confidential client)  | Yes      |
| `IDENTITY_BROKER_OBO_CLIENT_SECRET` | Client secret for the confidential token-exchange app       | Yes      |

Example:

```
IDENTITY_BROKER_VERIFY_BASE_URL=https://tenant.verify.ibm.com
IDENTITY_BROKER_OBO_CLIENT_ID=<redacted-obo-client-id>
IDENTITY_BROKER_OBO_CLIENT_SECRET=<redacted-obo-client-secret>
```

These must be set before starting the service. Missing values will cause a startup configuration error.

---



| Metric              | Target            |
| ------------------- | ----------------- |
| Cache hit latency   | <2ms              |
| API response        | <50ms (cache hit) |
| Verify call latency | <200ms            |

---

# 15. Concurrency

Service must support:

* multi-threaded execution
* async request handling
* safe cache access

FastAPI with **Uvicorn workers** recommended.

---

# 16. Observability

Metrics to expose:

* cache_hits
* cache_misses
* verify_requests
* verify_failures
* request_latency

Optional integration:

* Prometheus
* OpenTelemetry

---

# 17. Deployment

Recommended container deployment.

Example:

```
Docker
Kubernetes
```

Typical stack:

```
FastAPI
Uvicorn
Gunicorn (optional)
```

Container image guidance:

* Use a multi-stage Docker build.
* Install production dependencies in the builder stage with `uv sync --frozen --no-dev --no-install-project`.
* Copy the resulting dependency-only virtualenv into the runtime image together with the source directories. This is acceptable when the builder and runtime stages use the same Python base image and avoids reinstalling dependencies in the final stage.

---

# 18. Example API Call

Request:

```
POST /v1/identity/obo-token
```

```
curl -X POST http://localhost:8080/v1/identity/obo-token \
 -H "Content-Type: application/json" \
 -d '{
   "subject_token": "<redacted-subject-token>",
   "actor_token": "<redacted-actor-token>"
 }'
```

---

# 19. Future Enhancements

Potential improvements:

* Redis distributed cache
* mTLS authentication
* rate limiting
* service-to-service authentication
* JWT signature verification
* async HTTP client for IBM Verify (httpx)

---

# Summary

This system provides:

* A **Python identity broker service**
* A **high-performance REST API**
* **IBM Verify on-behalf-of (OBO) token exchange**
* **Smart in-memory caching**
* **production-grade resilience and security**

Powered by:

* **IBM Verify**
* **FastAPI**

---
