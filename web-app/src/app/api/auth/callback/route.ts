import { NextResponse } from 'next/server';
import { exchangeCode, extractUserInfo } from '@/lib/auth/oauth';
import { verifyIdToken, decodeUnverified } from '@/lib/auth/jwt';
import {
  clearPkceCookie,
  clearStateCookie,
  readPkceCookie,
  readStateCookie,
  setSession,
} from '@/lib/auth/session';
import { timingSafeEqualString } from '@/lib/auth/pkce';
import { STEP_UP_CHANNEL, STEP_UP_STATE_PREFIX, type StepUpResult } from '@/lib/auth/step-up';
import { withRequestContext } from '@/lib/log/with-request-context';
import { setPreferredUsername } from '@/lib/log/context';
import { getLogger } from '@/lib/log/logger';

const log = getLogger('api.auth.callback');

export const dynamic = 'force-dynamic';

function browserOrigin(req: Request, fallback: string): string {
  const host = req.headers.get('host');
  if (!host) return fallback;
  const proto =
    req.headers.get('x-forwarded-proto')?.split(',')[0]?.trim() ||
    new URL(fallback).protocol.replace(':', '') ||
    'http';
  return `${proto}://${host}`;
}

// Page the step-up popup lands on: tell the chat tab how it went, then close.
function stepUpPopupResponse(result: StepUpResult): NextResponse {
  const html = `<!doctype html><meta charset="utf-8"><title>Verification</title>
<p>${result === 'complete' ? 'Verified. You can close this window.' : 'Verification did not complete. You can close this window.'}</p>
<script>
  try { const ch = new BroadcastChannel(${JSON.stringify(STEP_UP_CHANNEL)}); ch.postMessage(${JSON.stringify(result)}); ch.close(); } catch (e) {}
  window.close();
</script>`;
  return new NextResponse(html, { status: 200, headers: { 'Content-Type': 'text/html; charset=utf-8' } });
}

export const GET = withRequestContext(async (req) => {
  const url = new URL(req.url);
  const origin = browserOrigin(req, url.origin);
  const code = url.searchParams.get('code');
  const stateParam = url.searchParams.get('state');
  const errorParam = url.searchParams.get('error');

  const isStepUp = !!stateParam?.startsWith(STEP_UP_STATE_PREFIX);

  if (errorParam) {
    log.warn({ error: errorParam }, 'OAuth provider returned an error');
    if (isStepUp) return stepUpPopupResponse('failed');
    return NextResponse.redirect(new URL('/?error=' + encodeURIComponent(errorParam), origin));
  }

  if (!code || !stateParam) {
    log.warn('Missing code or state in callback');
    return new NextResponse('Missing code or state', { status: 400 });
  }

  const expectedState = await readStateCookie();
  const verifier = await readPkceCookie();

  if (!expectedState || !verifier || !timingSafeEqualString(stateParam, expectedState)) {
    log.warn('State or PKCE verifier missing/mismatch');
    await clearStateCookie();
    await clearPkceCookie();
    if (isStepUp) return stepUpPopupResponse('failed');
    return new NextResponse('Invalid OAuth state', { status: 400 });
  }

  try {
    const tokens = await exchangeCode(code, verifier);
    const claims = await verifyIdToken(tokens.id_token);
    const userInfo = extractUserInfo(claims);
    const accessClaims = decodeUnverified(tokens.access_token);
    const preferredUsername =
      (typeof accessClaims.preferred_username === 'string' && accessClaims.preferred_username) ||
      (typeof claims.preferred_username === 'string' && claims.preferred_username) ||
      '';

    if (preferredUsername) setPreferredUsername(preferredUsername);

    const expiresAt = Math.floor(Date.now() / 1000) + (tokens.expires_in ?? 3600);

    await setSession({
      access_token: tokens.access_token,
      id_token: tokens.id_token,
      expires_at: expiresAt,
      user_info: userInfo,
      preferred_username: preferredUsername,
    });

    await clearStateCookie();
    await clearPkceCookie();

    log.info({ stepUp: isStepUp }, 'Authentication completed successfully');
    if (isStepUp) return stepUpPopupResponse('complete');
    return NextResponse.redirect(new URL('/landing', origin), { status: 302 });
  } catch (err) {
    log.error({ err: String(err) }, 'OAuth callback failed');
    await clearStateCookie();
    await clearPkceCookie();
    if (isStepUp) return stepUpPopupResponse('failed');
    return new NextResponse('Authentication failed', { status: 500 });
  }
});
