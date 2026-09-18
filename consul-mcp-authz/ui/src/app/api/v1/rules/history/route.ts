import { NextResponse } from 'next/server';
import { api, ApiError, getApiUrl } from '@/lib/api';

export async function GET() {
  try {
    const data = await api.getHistory();
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
