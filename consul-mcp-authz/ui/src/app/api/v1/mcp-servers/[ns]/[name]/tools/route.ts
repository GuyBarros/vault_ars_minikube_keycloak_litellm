import { NextResponse } from 'next/server';
import { api, ApiError, getApiUrl } from '@/lib/api';

type Params = { ns: string; name: string };

export async function GET(
  _req: Request,
  { params }: { params: Promise<Params> },
) {
  const { ns, name } = await params;
  try {
    const data = await api.getMcpServerTools(ns, name);
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
