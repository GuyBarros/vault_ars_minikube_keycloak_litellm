import { describe, it, expect } from 'vitest';
import { decodeJwtPayload, formatClaimValue } from '@/lib/jwt-decode';

const SAMPLE = (() => {
  // header.payload.sig — payload encodes {sub: "alice", iat: 1700000000}
  const header = Buffer.from(JSON.stringify({ alg: 'RS256', typ: 'JWT' })).toString('base64url');
  const payload = Buffer.from(
    JSON.stringify({ sub: 'alice', iat: 1700000000, scope: 'a b c' }),
  ).toString('base64url');
  return `${header}.${payload}.signature`;
})();

describe('decodeJwtPayload', () => {
  it('decodes a JWT payload', () => {
    expect(decodeJwtPayload(SAMPLE)).toMatchObject({ sub: 'alice', iat: 1700000000 });
  });

  it('returns null on malformed token', () => {
    expect(decodeJwtPayload('not-a-jwt')).toBeNull();
    expect(decodeJwtPayload('')).toBeNull();
  });
});

describe('formatClaimValue', () => {
  it('formats unix timestamp seconds as ISO date', () => {
    const out = formatClaimValue(1700000000);
    expect(out).toMatch(/^\d{4}-\d{2}-\d{2}T/);
  });

  it('formats arrays as comma-separated strings', () => {
    expect(formatClaimValue(['a', 'b'])).toBe('a, b');
  });

  it('formats arrays of objects as JSON, not [object Object]', () => {
    const out = formatClaimValue([{ type: 'vault:path_access', path: 'database/creds/*' }]);
    expect(out).toBe('{"type":"vault:path_access","path":"database/creds/*"}');
  });

  it('handles primitives', () => {
    expect(formatClaimValue('hi')).toBe('hi');
    expect(formatClaimValue(42)).toBe('42');
    expect(formatClaimValue(undefined)).toBe('');
  });
});
