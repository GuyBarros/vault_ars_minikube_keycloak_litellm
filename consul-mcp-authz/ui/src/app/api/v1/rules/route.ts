import { NextResponse } from 'next/server';
import { api, ApiError, getApiUrl } from '@/lib/api';

// Browser-facing proxy. Today the API has no auth (P1.5 will add OIDC),
// so this route just forwards; the indirection exists so we can drop
// auth headers / cookies in one place when the time comes.
export async function GET() {
  try {
    const data = await api.getRules();
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
