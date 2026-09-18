# OPA Policy Studio (Frontend-Only PoC)

A React + TypeScript UI for managing and evaluating OPA policies directly from the browser.

## Features

- Three-panel layout:
  - Left: policy explorer (list, create, refresh, delete)
  - Center: policy editor (Monaco + Rego syntax highlighting)
  - Right: evaluation panel (input/output + presets)
- Resizable center editor pane
- Light/Dark theme toggle (persisted in `localStorage`)
- Policy save via `Ctrl/Cmd + S`
- Evaluation input accepts JSON or RAW text
- Evaluation input is always UTF-8 base64 encoded and sent as `{"input":"<base64_value>"}`
- Data path auto-populates from selected policy package path (`ast.package.path`, with raw fallback)
- Built-in test input presets with categories:
  - Prompt Injection
  - Code Safety
  - PII Data
- Format JSON action for evaluation input

## Requirements

- Node.js 20+
- OPA reachable from browser
- CORS enabled on OPA for local PoC

```bash
opa run --server --addr :8080 --cors-allowed-origins="*"
```

## Configuration

By default in development, frontend calls `/opa/*` and Vite proxies to:

- `VITE_OPA_PROXY_TARGET` (default: `http://localhost:8080`)

Create `.env.local`:

```bash
VITE_OPA_PROXY_TARGET=http://localhost:8080
```

Optional direct mode:

```bash
VITE_OPA_USE_DIRECT=true
VITE_OPA_BASE_URL=http://localhost:8080
```

## Run

```bash
npm install
npm run dev
```

After env changes, restart `npm run dev`.

## Validate

```bash
npm run lint
npm run build
```

## Evaluation request behavior

The editor accepts JSON or RAW text:

- valid JSON -> normalized (`JSON.stringify`) then encoded
- invalid JSON -> treated as RAW and encoded as-is

Final request body:

```json
{
  "input": "<base64_value>"
}
```

Example:

```bash
curl -X POST http://localhost:8080/v1/data/app/security -d '{"input":"eyJjbWQiOiAiY2F0IC9ldGMvcGFzc3dkIn0="}'
```

### Data path normalization

Evaluation path is normalized to avoid duplicate prefixes. Inputs like:

- `app/authz`
- `data/app/authz`
- `/v1/data/app/authz`

all evaluate against the same OPA endpoint path under `/v1/data/...`.
