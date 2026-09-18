import { AsyncLocalStorage } from 'node:async_hooks';
import { randomUUID } from 'node:crypto';

export interface RequestContext {
  request_id: string;
  client_ip: string;
  request_path: string;
  preferred_username: string;
}

const storage = new AsyncLocalStorage<RequestContext>();

export function runWithRequestContext<T>(ctx: RequestContext, fn: () => T): T {
  return storage.run(ctx, fn);
}

export function getRequestContext(): RequestContext {
  return (
    storage.getStore() ?? {
      request_id: '-',
      client_ip: '-',
      request_path: '-',
      preferred_username: '',
    }
  );
}

export function setPreferredUsername(name: string): void {
  const ctx = storage.getStore();
  if (ctx) ctx.preferred_username = name;
}

export function getRequestId(): string {
  const ctx = storage.getStore();
  if (ctx?.request_id && ctx.request_id !== '-') return ctx.request_id;
  return randomUUID();
}

const CLIENT_IP_HEADERS = ['x-forwarded-for', 'x-real-ip', 'cf-connecting-ip'] as const;
const REQUEST_ID_HEADER = 'x-request-id';

export function extractClientIp(headers: Headers): string {
  for (const name of CLIENT_IP_HEADERS) {
    const raw = headers.get(name);
    if (raw && raw.trim()) return raw.split(',')[0]!.trim();
  }
  return '-';
}

export function extractIncomingRequestId(headers: Headers): string {
  const raw = headers.get(REQUEST_ID_HEADER);
  return raw && raw.trim() ? raw.trim() : randomUUID();
}

export const REQUEST_ID_HEADER_NAME = 'X-Request-ID';
