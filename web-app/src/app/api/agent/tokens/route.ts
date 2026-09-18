import { NextResponse } from 'next/server';
import { getSession } from '@/lib/auth/session';
import { getAgentTokens } from '@/lib/agent/client';
import { withRequestContext } from '@/lib/log/with-request-context';
import { getLogger } from '@/lib/log/logger';

const log = getLogger('api.agent.tokens');

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

export const GET = withRequestContext(async () => {
  const session = await getSession();
  if (!session?.access_token) {
    return NextResponse.json({ error: 'unauthenticated' }, { status: 401 });
  }

  try {
    const tokens = await getAgentTokens(session.access_token);
    return NextResponse.json(tokens, { status: 200 });
  } catch (err) {
    log.error({ err: String(err) }, 'Failed to fetch agent tokens');
    return NextResponse.json(
      { error: 'agent_tokens_unavailable', detail: String(err) },
      { status: 502 },
    );
  }
});
