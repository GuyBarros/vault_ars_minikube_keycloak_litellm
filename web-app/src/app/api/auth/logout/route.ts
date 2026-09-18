import { NextResponse } from 'next/server';
import { buildLogoutUrl } from '@/lib/auth/oauth';
import { clearSession, getSession } from '@/lib/auth/session';
import { withRequestContext } from '@/lib/log/with-request-context';
import { getLogger } from '@/lib/log/logger';

const log = getLogger('api.auth.logout');

export const dynamic = 'force-dynamic';

export const GET = withRequestContext(async () => {
  const s = await getSession();
  const idToken = s?.id_token ?? '';
  const url = buildLogoutUrl(idToken);
  log.info('Logging out current session');
  await clearSession();
  return NextResponse.redirect(url, { status: 302 });
});
