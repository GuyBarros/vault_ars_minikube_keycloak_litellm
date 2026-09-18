# OPA Policy Studio - Frontend-Only PoC Specification

## 1. Purpose

Provide a browser-based UI to manage and evaluate OPA policies without a backend service.

## 2. Scope

### Included

- Direct browser-to-OPA API integration
- Policy CRUD UI
- Rego policy editing with syntax highlighting
- Policy evaluation with editable test input
- Theme toggle (light/dark)
- Prompt testing presets (categorized)

### Excluded

- Backend/API gateway
- Authentication/authorization
- Audit logs
- Rate limiting and tenant isolation

## 3. Architecture

```text
Browser (React + Vite + Zustand + Monaco)
      |
      | HTTP
      v
OPA Server (/v1/policies, /v1/data/*)
```

## 4. Runtime Requirements

- Node.js 20+
- OPA with CORS enabled

```bash
opa run --server --addr :8080 --cors-allowed-origins="*"
```

## 5. Technology Stack

- React + TypeScript
- Vite
- Zustand
- Monaco Editor
- Tailwind CSS
- Axios

## 6. UI Layout

- Left panel: Policy Explorer
- Center panel: Policy Editor (horizontally resizable)
- Right panel: Evaluation Panel

## 7. Functional Requirements

### 7.1 Policy Explorer

- List policies from `GET /v1/policies`
- Create policy (`PUT /v1/policies/{id}`)
- Delete policy (`DELETE /v1/policies/{id}`)
- Refresh list
- Select policy to load in editor

### 7.2 Policy Editor

- Display selected policy source
- Rego syntax highlighting in Monaco
- Save with button and `Ctrl/Cmd + S`
- Save to `PUT /v1/policies/{id}`

### 7.3 Evaluation Panel

- Data Path textbox (editable)
- Auto-fill Data Path from selected policy package path:
  - Prefer `result.ast.package.path[].value`
  - Fallback to `package ...` parsed from raw policy text
- Input editor (JSON or RAW string)
- Format JSON button for pretty-printing valid JSON input
- Result panel below input panel
- Run button triggers `POST /v1/data/{path}`

### 7.4 Preset Inputs

- Category dropdown:
  - Prompt Injection
  - Code Safety
  - PII Data
- Preset dropdown filtered by category
- Selecting preset populates input editor
- Input remains editable after preset selection
- Selected preset trigger shown for context

### 7.5 Theme

- Header toggle for dark/light theme
- Theme persists in `localStorage`
- Monaco editor theme follows selected app theme

## 8. API Contracts

| Action | Endpoint |
| --- | --- |
| List policies | `GET /v1/policies` |
| Get policy | `GET /v1/policies/{id}` |
| Upsert policy | `PUT /v1/policies/{id}` |
| Delete policy | `DELETE /v1/policies/{id}` |
| Evaluate policy | `POST /v1/data/{path}` |

## 9. Evaluation Input/Output Rules

### Input transformation

1. Read input editor text
2. If valid JSON: canonicalize with `JSON.stringify`
3. Else: treat as raw text
4. Encode UTF-8 bytes as base64
5. Send body:

```json
{
  "input": "<base64_value>"
}
```

### Path normalization

Before request, strip leading prefixes to avoid duplication:

- `/`
- `v1/data/`
- `data/`
- full base URL prefix (if entered by user)

This prevents accidental `/v1/data/data/...`.

## 10. State Model (Zustand)

Core state includes:

- `policies`
- `selectedPolicyId`
- `policyContent`
- `evaluationPath`
- `evaluationInput`
- `evaluationResult`
- `theme`
- loading/saving/evaluating flags
- error message for policy loading

## 11. Error Handling

- Toast notifications for user-visible failures
- Friendly OPA error mapping in API client
- Result panel always renders a safe textual representation

## 12. Developer Commands

```bash
npm install
npm run dev
npm run lint
npm run build
```

## 13. Security Note (PoC)

This is a local/dev-focused PoC:

- OPA is directly reachable from browser
- No auth boundary is provided by this app
- Presets are test payloads only; no server-side enforcement is included
