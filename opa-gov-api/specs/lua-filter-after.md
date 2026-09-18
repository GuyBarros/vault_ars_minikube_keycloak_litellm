# Simplified Envoy Lua filter — post-`opa-gov-api` sketch

> **Status:** documentation only. Do not apply to `deploy-k8s/service-defaults-agent-opa.yaml` until `opa-gov-api` is built, pushed, deployed, and the Consul `ServiceDefaults` + `ServiceIntentions` entries for `opa-gov-api` are in place.

This file sketches the Lua filter that will replace lines 60–197 of `deploy-k8s/service-defaults-agent-opa.yaml` once `opa-gov-api` is live in the mesh. All manual base64 handling, JSON parsing, brace-matching, and policy-path routing moves out of Lua and into the Python service.

## Assumptions for the follow-up

- `opa-gov-api` is deployed as a Consul-connect service reachable at an FQDN resolvable from Envoy's outbound cluster — e.g. `opa-gov-api.default.svc.cluster.local` for a plain k8s Service, or a Consul intention FQDN like `opa-gov-api.<dc>.internal.<trust-domain>`.
- A `ServiceIntentions` entry allows `ai-agent` → `opa-gov-api`.
- The service returns:
  - `/evaluate` → `200` (allowed) or `400` (blocked, body is the block message).
  - `/mask` → `200` with the masked body (plain text or JSON per `Content-Type`).

## Lua sketch

```lua
local OPA_GOV_SERVICE = "opa-gov-api.default.svc.cluster.local"
local OPA_GOV_TIMEOUT_MS = 5000
local OPA_GOV_PATH_EVALUATE = "/evaluate"
local OPA_GOV_PATH_MASK = "/mask"

local function call_gov(handle, path, body_str)
  local headers = {
    [":method"] = "POST",
    [":path"] = path,
    [":authority"] = OPA_GOV_SERVICE,
    ["content-type"] = "text/plain",
  }
  local status_headers, response_body = handle:httpCall(
    OPA_GOV_SERVICE,
    headers,
    body_str or "",
    OPA_GOV_TIMEOUT_MS
  )
  return status_headers, response_body
end

function envoy_on_request(request_handle)
  request_handle:logInfo("Inbound Request")
  local body = request_handle:body(true)
  if not body or body:length() == 0 then return end
  local body_str = body:getBytes(0, body:length())

  local status_headers, response_body = call_gov(
    request_handle, OPA_GOV_PATH_EVALUATE, body_str
  )
  if not status_headers then
    request_handle:logWarn("opa-gov-api unreachable on /evaluate; fail-open")
    return
  end

  local status = tonumber(status_headers[":status"])
  if status == 400 then
    request_handle:respond(
      {[":status"] = "400", ["content-type"] = "text/plain"},
      response_body or "This content was blocked due to security policy violation"
    )
  end
  -- any other status (including 200) -> let the request through
end

function envoy_on_response(response_handle)
  response_handle:logInfo("Sending Response")

  local status = tonumber(response_handle:headers():get(":status"))
  if status ~= 200 then
    response_handle:logInfo("Skipping mask for non-200 response: " .. tostring(status))
    return
  end

  local body = response_handle:body(true)
  if not body or body:length() == 0 then return end
  local body_str = body:getBytes(0, body:length())

  local status_headers, response_body = call_gov(
    response_handle, OPA_GOV_PATH_MASK, body_str
  )
  if not status_headers or not response_body then
    response_handle:logWarn("opa-gov-api unreachable on /mask; passing original body through")
    return
  end

  response_handle:body():setBytes(response_body)
  response_handle:headers():replace(
    "content-length", tostring(response_handle:body():length())
  )
  local gov_ct = status_headers["content-type"]
  if gov_ct then
    response_handle:headers():replace("content-type", gov_ct)
  end
end
```

## What this removes from the current filter

- `call_opa(...)` helper — replaced by `call_gov`.
- `extract_result_object(...)` — the manual `"result":{...}` brace-matching is gone.
- `is_blocked(...)` — blocking is now a simple `status == 400` check.
- `resolve_opa_service(...)` — no more Consul dynamic-metadata parsing for the upstream. The Lua hard-codes (or templates) the `opa-gov-api` service name; `opa-gov-api` itself talks to OPA using its own `OPA_BASE_URL` env var.
- Base64 encoding (`handle:base64Escape(body_str)`) and `{"input":"..."}` wrapping — moved into `opa-gov-api`.
- The `response_handle:headers():replace("content-type", "application/json")` hardcode — we now propagate whatever Content-Type `opa-gov-api` returned, which correctly distinguishes plain-text and JSON masking outputs.

## Migration notes

- `opa-server.yaml` stays as-is. OPA continues to serve the same rego endpoints; only the caller changes.
- `service-intentions.yaml` needs a new entry for `ai-agent` → `opa-gov-api`. The existing `ai-agent` → `opa-service` entry can stay until verified and then removed.
- `opa-gov-api` needs its own `Deployment` / `Service` / `ServiceDefaults`.
- Envoy's cluster for `opa-gov-api` must be reachable from the `ai-agent` proxy. If you use the Consul FQDN form, set `[":authority"]` to the same FQDN and ensure an intention allows it.
