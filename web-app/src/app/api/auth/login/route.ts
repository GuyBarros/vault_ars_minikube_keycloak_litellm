import { NextResponse } from 'next/server';
import { buildAuthorizeUrl } from '@/lib/auth/oauth';
import { challengeFromVerifier, generateState, generateVerifier } from '@/lib/auth/pkce';
import { setPkceCookie, setStateCookie } from '@/lib/auth/session';
import { STEP_UP_STATE_PREFIX } from '@/lib/auth/step-up';
import { withRequestContext } from '@/lib/log/with-request-context';
import { getLogger } from '@/lib/log/logger';

const log = getLogger('api.auth.login');

export const dynamic = 'force-dynamic';

export const GET = withRequestContext(async (req) => {
  const stepUp = new URL(req.url).searchParams.get('stepup') === '1';
  const state = (stepUp ? STEP_UP_STATE_PREFIX : '') + generateState();
  const verifier = generateVerifier();
  const challenge = challengeFromVerifier(verifier);

  await setStateCookie(state);
  await setPkceCookie(verifier);

  const url = buildAuthorizeUrl({ state, codeChallenge: challenge, acr: stepUp ? '2' : undefined });
  log.debug('Redirecting to Keycloak authorize endpoint');
  return NextResponse.redirect(url, { status: 302 });
});
