import { describe, it, expect } from 'vitest';
import { buildAuthorizeUrl, buildLogoutUrl, extractUserInfo } from '@/lib/auth/oauth';

describe('buildAuthorizeUrl', () => {
  it('includes all required OAuth params with PKCE', () => {
    const url = new URL(buildAuthorizeUrl({ state: 'st-123', codeChallenge: 'challenge-xyz' }));
    const p = url.searchParams;
    expect(p.get('response_type')).toBe('code');
    expect(p.get('state')).toBe('st-123');
    expect(p.get('code_challenge')).toBe('challenge-xyz');
    expect(p.get('code_challenge_method')).toBe('S256');
    expect(p.get('client_id')).toBeTruthy();
    expect(p.get('redirect_uri')).toBeTruthy();
    expect(p.get('scope')).toBeTruthy();
  });
});

describe('buildLogoutUrl', () => {
  it('appends id_token_hint when present', () => {
    const url = new URL(buildLogoutUrl('id-token-abc'));
    expect(url.searchParams.get('id_token_hint')).toBe('id-token-abc');
    expect(url.searchParams.get('post_logout_redirect_uri')).toBeTruthy();
  });

  it('omits id_token_hint when empty', () => {
    const url = new URL(buildLogoutUrl(''));
    expect(url.searchParams.has('id_token_hint')).toBe(false);
  });
});

describe('extractUserInfo', () => {
  it('uses name when present and computes initials from first two words', () => {
    expect(extractUserInfo({ name: 'John Doe Smith', email: 'j@x.com', sub: '1' })).toEqual({
      name: 'John Doe Smith',
      email: 'j@x.com',
      initials: 'JD',
      sub: '1',
    });
  });

  it('falls back to preferred_username then sub then "User"', () => {
    expect(extractUserInfo({ preferred_username: 'alice', sub: '2' }).name).toBe('alice');
    expect(extractUserInfo({ sub: 'sub-only' }).name).toBe('sub-only');
    expect(extractUserInfo({}).name).toBe('User');
  });

  it('returns ? initials for empty name parts', () => {
    expect(extractUserInfo({ name: '   ', sub: '1' }).initials).toBe('?');
  });
});
