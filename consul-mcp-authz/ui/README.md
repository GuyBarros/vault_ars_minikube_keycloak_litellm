# consul-mcp-authz-ui

Operator console for the `consul-mcp-authz` catalog. Built to match
Consul's own UI conventions — dark sidebar, white content surface, the
green Allow pill, the blue "Create" CTA, Inter typography.

## P2 pilot scope (this slice)

| Surface | Status |
|---------|--------|
| Rules list (`GET /v1/rules`) | ✅ end-to-end |
| Create rule | ⬜ next slice |
| View / edit a rule pair | ⬜ next slice |
| History + rollback | ⬜ next slice |
| Agents / MCP Servers browse | ⬜ next slice |
| OIDC auth | ⬜ P1.5 |

The Rules screen is rendered server-side; the page calls
`api.getRules()` directly so the API URL stays on the server, not in
the browser bundle. `/api/v1/rules` is the same call exposed as a
proxy for future client-side fetches.

## Stack

- Next.js 15.5 (App Router) + React 19, TypeScript strict
- TailwindCSS with Consul-derived design tokens (see
  [`tailwind.config.ts`](./tailwind.config.ts))
- Inter from `next/font/google`
- `output: 'standalone'` in `next.config.mjs` so the Docker image runs
  on a stock `node:20-bookworm-slim` with no `next start` shim

No IBM Carbon. The chrome is deliberately tuned to look like Consul,
which is the system operators of this stack already use day to day.

## Packaging

The UI does **not** have its own Docker image. It's built from the
parent `consul-mcp-authz/Dockerfile` and packaged into the single
`panchalravi/consul-mcp-authz` image alongside the FastAPI backend.
Inside the running container, supervisord runs `uvicorn` on `:8080` and
the Next.js standalone server on `:8502`; the UI talks to the API at
`http://127.0.0.1:8080` over loopback. See the parent
[README](../README.md) for image build + deploy steps.

## Local development

```bash
cd consul-mcp-authz/ui
npm install
npm run dev   # http://localhost:8502, auto-reloads
```

Point at a running API (it's not running inside the dev server here):

```bash
# In another shell — port-forward the API on the existing service.
kubectl port-forward svc/consul-mcp-authz 8080:8080

# Then run the UI:
MCP_AUTHZ_UI_API_URL=http://localhost:8080 npm run dev
```

The default `MCP_AUTHZ_UI_API_URL` is `http://127.0.0.1:8080` — the
correct loopback target when the UI runs alongside the API in the same
container in-cluster. Override it explicitly for local dev so the dev
server reaches your port-forwarded API instead.

## Sidebar parity with Consul

The left rail copies the Consul layout: logo · datacenter chip ·
admin-partition + namespace selectors · nav menu. Because this app has
no multi-tenant model of its own, the partition/namespace selectors are
intentionally inert — they exist for visual parity. They will become
live when the catalog gains namespace support.
