import { NextResponse } from 'next/server';
import { api, ApiError, getApiUrl } from '@/lib/api';

type Params = { srcNs: string; srcSvc: string; dstNs: string; dstSvc: string };

export async function PATCH(
  req: Request,
  { params }: { params: Promise<Params> },
) {
  const { srcNs, srcSvc, dstNs, dstSvc } = await params;

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ detail: 'Body must be JSON.' }, { status: 400 });
  }

  if (
    !body ||
    typeof body !== 'object' ||
    !Array.isArray((body as { allow?: unknown }).allow) ||
    !(body as { allow: unknown[] }).allow.every((t) => typeof t === 'string')
  ) {
    return NextResponse.json(
      { detail: 'Body must be { "allow": string[], "expected_version": number? }.' },
      { status: 400 },
    );
  }

  const { allow, expected_version } = body as {
    allow: string[];
    expected_version?: number | null;
  };

  try {
    const result = await api.patchRulePair(srcNs, srcSvc, dstNs, dstSvc, {
      allow,
      expected_version: expected_version ?? null,
    });
    return NextResponse.json(result);
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
