import { jwtVerify, decodeJwt } from 'jose';
import { config } from '@/lib/config';
import { getJwks } from '@/lib/auth/jwks';

export interface IdTokenClaims {
  sub?: string;
  name?: string;
  email?: string;
  preferred_username?: string;
  iss?: string;
  aud?: string | string[];
  iat?: number;
  exp?: number;
  amr?: string[];
  [key: string]: unknown;
}

export async function verifyIdToken(idToken: string): Promise<IdTokenClaims> {
  const { payload } = await jwtVerify(idToken, getJwks(), {
    audience: config.KEYCLOAK_CLIENT_ID,
    algorithms: ['RS256'],
  });
  return payload as IdTokenClaims;
}

// Access tokens minted for the chat hop use aud=token-exchange (the OBO
// broker), not the web client id. Re-checked on every /api/agent/query.
export async function verifyAccessToken(accessToken: string): Promise<IdTokenClaims> {
  const { payload } = await jwtVerify(accessToken, getJwks(), {
    issuer: `${config.KEYCLOAK_BASE_URL}/realms/${config.KEYCLOAK_REALM}`,
    audience: 'token-exchange',
    algorithms: ['RS256'],
  });
  return payload as IdTokenClaims;
}

export function decodeUnverified(token: string): Record<string, unknown> {
  try {
    return decodeJwt(token);
  } catch {
    return {};
  }
}
