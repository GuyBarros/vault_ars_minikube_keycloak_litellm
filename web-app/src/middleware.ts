import { NextResponse, type NextRequest } from 'next/server';
import { COOKIE_NAMES } from '@/lib/auth/cookies';

const PROTECTED_PAGE_PATHS = ['/landing'];
const PROTECTED_API_PATHS = ['/api/agent', '/api/auth/claims', '/api/auth/me'];
const STATE_CHANGING_API_PATHS = ['/api/agent'];

function isProtectedPage(pathname: string): boolean {
  return PROTECTED_PAGE_PATHS.some((p) => pathname === p || pathname.startsWith(p + '/'));
}

function isProtectedApi(pathname: string): boolean {
  return PROTECTED_API_PATHS.some((p) => pathname === p || pathname.startsWith(p + '/'));
}

function isStateChangingApi(pathname: string): boolean {
  return STATE_CHANGING_API_PATHS.some((p) => pathname === p || pathname.startsWith(p + '/'));
}

function expectedOrigin(req: NextRequest): string | null {
  const host = req.headers.get('host');
  if (!host) return null;
  const proto =
    req.headers.get('x-forwarded-proto')?.split(',')[0]?.trim() ||
    req.nextUrl.protocol.replace(':', '') ||
    'http';
  return `${proto}://${host}`;
}

function isSameOriginRequest(req: NextRequest): boolean {
  const expected = expectedOrigin(req);
  if (!expected) return false;
  const origin = req.headers.get('origin');
  if (origin) return origin === expected;
  const referer = req.headers.get('referer');
  if (referer) {
    try {
      return new URL(referer).origin === expected;
    } catch {
      return false;
    }
  }
  return false;
}

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;

  if (
    isStateChangingApi(pathname) &&
    req.method !== 'GET' &&
    req.method !== 'HEAD' &&
    !isSameOriginRequest(req)
  ) {
    return new NextResponse('Forbidden: cross-origin request', { status: 403 });
  }

  const hasSession = req.cookies.has(COOKIE_NAMES.session);

  if (isProtectedApi(pathname) && !hasSession) {
    return NextResponse.json({ error: 'unauthenticated' }, { status: 401 });
  }

  if (isProtectedPage(pathname) && !hasSession) {
    const url = req.nextUrl.clone();
    url.pathname = '/';
    url.search = '';
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ['/landing/:path*', '/api/agent/:path*', '/api/auth/claims', '/api/auth/me'],
};
