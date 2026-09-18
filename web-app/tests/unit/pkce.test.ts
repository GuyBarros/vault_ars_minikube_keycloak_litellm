import { describe, it, expect } from 'vitest';
import { createHash } from 'node:crypto';
import {
  challengeFromVerifier,
  generateState,
  generateVerifier,
  timingSafeEqualString,
} from '@/lib/auth/pkce';

describe('pkce', () => {
  it('generateVerifier produces a base64url string of expected length', () => {
    const v = generateVerifier();
    expect(v).toMatch(/^[A-Za-z0-9_-]+$/);
    expect(v.length).toBeGreaterThanOrEqual(43);
    expect(v.length).toBeLessThanOrEqual(128);
  });

  it('challengeFromVerifier matches SHA-256 base64url of verifier', () => {
    const v = 'verifier-abc-123';
    const expected = createHash('sha256').update(v).digest('base64url');
    expect(challengeFromVerifier(v)).toBe(expected);
  });

  it('generateState produces a non-empty base64url string', () => {
    const s = generateState();
    expect(s).toMatch(/^[A-Za-z0-9_-]+$/);
    expect(s.length).toBeGreaterThanOrEqual(20);
  });
});

describe('timingSafeEqualString', () => {
  it('matches identical strings', () => {
    expect(timingSafeEqualString('abc', 'abc')).toBe(true);
  });

  it('rejects different strings of equal length', () => {
    expect(timingSafeEqualString('abc', 'abd')).toBe(false);
  });

  it('rejects strings of different length', () => {
    expect(timingSafeEqualString('abc', 'abcd')).toBe(false);
  });
});
