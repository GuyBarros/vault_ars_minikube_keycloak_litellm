// Server-side helpers that talk to the upstream consul-mcp-authz.
// Used by Next.js route handlers (the browser never reaches the API
// directly — every request is proxied so we can centralise auth in P1.5).

import type {
  AgentList,
  CatalogRead,
  CatalogWriteResponse,
  HistoryResponse,
  McpServerList,
  McpServerTools,
} from './types';

// The UI and API ship in the same container; the UI's Next server
// talks to uvicorn over loopback. Override with MCP_AUTHZ_UI_API_URL
// when running the UI on its own (e.g., `npm run dev` against a
// port-forwarded API).
const DEFAULT_API_URL = 'http://127.0.0.1:8080';

export function getApiUrl(): string {
  return process.env.MCP_AUTHZ_UI_API_URL?.trim() || DEFAULT_API_URL;
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${getApiUrl()}${path}`;
  const res = await fetch(url, {
    ...init,
    headers: {
      Accept: 'application/json',
      ...(init?.headers ?? {}),
    },
    // The catalog mutates often via PUT/PATCH from this same UI; we never
    // want a cached read sneaking back to the browser.
    cache: 'no-store',
  });

  const text = await res.text();
  let body: unknown = undefined;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }

  if (!res.ok) {
    const detail =
      typeof body === 'object' && body && 'detail' in body
        ? String((body as { detail: unknown }).detail)
        : (typeof body === 'string' && body) || `Upstream returned ${res.status}`;
    throw new ApiError(res.status, detail);
  }

  return body as T;
}

function encodeKey(part: string): string {
  return part.split('/').map(encodeURIComponent).join('/');
}

export const api = {
  getRules: () => call<CatalogRead>('/v1/rules'),

  getAgents: () => call<AgentList>('/v1/agents'),

  getMcpServers: () => call<McpServerList>('/v1/mcp-servers'),

  getMcpServerTools: (ns: string, name: string) =>
    call<McpServerTools>(`/v1/mcp-servers/${encodeKey(ns)}/${encodeKey(name)}/tools`),

  patchRulePair: (
    srcNs: string,
    srcSvc: string,
    dstNs: string,
    dstSvc: string,
    body: { allow: string[]; expected_version: number | null },
  ) =>
    call<CatalogWriteResponse>(
      `/v1/rules/${encodeKey(srcNs)}/${encodeKey(srcSvc)}/${encodeKey(dstNs)}/${encodeKey(
        dstSvc,
      )}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
    ),

  getHistory: () => call<HistoryResponse>('/v1/rules/history'),

  postRollback: (body: { version: number; expected_version: number | null }) =>
    call<CatalogWriteResponse>('/v1/rules:rollback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
};
