import { describe, it, expect } from 'vitest';
import { loaState } from '@/lib/auth/loa';

const NOW = 1_000_000;

describe('loaState', () => {
  it('is baseline for a password login', () => {
    expect(loaState({ acr: '1', iat: NOW - 5 }, 300, NOW)).toEqual({ level: 1, elevatedUntil: null, ttlSeconds: 300 });
  });

  it('is elevated for one window after the step-up', () => {
    expect(loaState({ acr: '2', iat: NOW - 60 }, 300, NOW)).toEqual({ level: 2, elevatedUntil: NOW + 240, ttlSeconds: 300 });
  });

  it('drops back to baseline once the window has lapsed, even though the token is still valid', () => {
    expect(loaState({ acr: '2', iat: NOW - 301 }, 300, NOW)).toEqual({ level: 1, elevatedUntil: null, ttlSeconds: 300 });
  });

  it('prefers acr_time over iat', () => {
    expect(loaState({ acr: '2', acr_time: NOW - 10, iat: NOW - 9999 }, 300, NOW).elevatedUntil).toBe(NOW + 290);
  });

  it('treats a missing acr or time as baseline', () => {
    expect(loaState({}, 300, NOW).level).toBe(1);
    expect(loaState({ acr: '2' }, 300, NOW).elevatedUntil).toBeNull();
  });
});
