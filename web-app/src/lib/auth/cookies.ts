import type { SessionOptions } from 'iron-session';
import { config } from '@/lib/config';

const isProd = process.env.NODE_ENV === 'production';

const baseCookie = {
  httpOnly: true,
  secure: isProd,
  sameSite: 'lax',
  path: '/',
} as const;

export const COOKIE_NAMES = {
  session: 'verify_session',
  tokens: 'verify_tokens',
  state: 'verify_oauth_state',
  pkce: 'verify_pkce_verifier',
  theme: 'verify_theme',
} as const;

export const sessionCookieOptions: SessionOptions = {
  password: config.SESSION_PASSWORD,
  cookieName: COOKIE_NAMES.session,
  cookieOptions: {
    ...baseCookie,
    path: '/',
    maxAge: 60 * 60 * 8,
  },
};

export const tokensCookieOptions: SessionOptions = {
  password: config.SESSION_PASSWORD,
  cookieName: COOKIE_NAMES.tokens,
  cookieOptions: {
    ...baseCookie,
    path: '/',
    maxAge: 60 * 60 * 8,
  },
};

export const stateCookieOptions: SessionOptions = {
  password: config.SESSION_PASSWORD,
  cookieName: COOKIE_NAMES.state,
  cookieOptions: {
    ...baseCookie,
    path: '/api/auth/callback',
    maxAge: 60 * 10,
  },
};

export const pkceCookieOptions: SessionOptions = {
  password: config.SESSION_PASSWORD,
  cookieName: COOKIE_NAMES.pkce,
  cookieOptions: {
    ...baseCookie,
    path: '/api/auth/callback',
    maxAge: 60 * 10,
  },
};
