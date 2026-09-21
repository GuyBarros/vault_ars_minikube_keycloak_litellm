import { NextResponse } from 'next/server';
import { config } from '@/lib/config';
import { getDecodedAccessToken, getSession } from '@/lib/auth/session';
import { loaState } from '@/lib/auth/loa';
import { withRequestContext } from '@/lib/log/with-request-context';

export const dynamic = 'force-dynamic';

export const GET = withRequestContext(async () => {
  const s = await getSession();
  if (!s?.user_info) {
    return NextResponse.json({ error: 'unauthenticated' }, { status: 401 });
  }
  const claims = (await getDecodedAccessToken()) ?? {};
  return NextResponse.json(
    {
      ...s.user_info,
      preferred_username: s.preferred_username ?? '',
      loa: loaState(claims, config.STEP_UP_TTL_SECONDS, Math.floor(Date.now() / 1000)),
    },
    { status: 200 },
  );
});
