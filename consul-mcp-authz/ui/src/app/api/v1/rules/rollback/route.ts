import { NextResponse } from 'next/server';
import { api, ApiError, getApiUrl } from '@/lib/api';

// Browser-facing proxy. The upstream endpoint uses a colon
// (`/v1/rules:rollback`) which is awkward in a filesystem-routed framework,
// so the browser path drops the colon and we re-introduce it in the api helper.
export async function POST(req: Request) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ detail: 'Body must be JSON.' }, { status: 400 });
  }

  if (
    !body ||
    typeof body !== 'object' ||
    typeof (body as { version?: unknown }).version !== 'number'
  ) {
    return NextResponse.json(
      { detail: 'Body must be { "version": number, "expected_version": number? }.' },
      { status: 400 },
    );
  }

  const { version, expected_version } = body as {
    version: number;
    expected_version?: number | null;
  };

  try {
    const data = await api.postRollback({
      version,
      expected_version: expected_version ?? null,
    });
    return NextResponse.json(data);
  } catch (err) {
    if (err instanceof ApiError) {
      return NextResponse.json({ detail: err.message }, { status: err.status });
    }
    return NextResponse.json(
      {
        detail: `Could not reach consul-mcp-authz at ${getApiUrl()}: ${
          err instanceof Error ? err.message : String(err)
        }`,
      },
      { status: 502 },
    );
  }
}
