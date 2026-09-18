import { z } from 'zod';

const schema = z.object({
  KEYCLOAK_CLIENT_ID: z.string().min(1, 'KEYCLOAK_CLIENT_ID is required'),
  KEYCLOAK_CLIENT_SECRET: z.string().min(1, 'KEYCLOAK_CLIENT_SECRET is required'),
  // Browser-facing: used for the authorize/logout redirects sent to the
  // user's browser, which is outside the cluster and cannot resolve
  // in-cluster service DNS.
  KEYCLOAK_BASE_URL: z
    .string()
    .url('KEYCLOAK_BASE_URL must be a valid URL')
    .transform((s) => s.replace(/\/$/, '')),
  // Server-side: used for the code<->token exchange and JWKS fetch, which
  // run from inside the cluster — defaults to KEYCLOAK_BASE_URL when unset.
  KEYCLOAK_INTERNAL_BASE_URL: z
    .string()
    .url('KEYCLOAK_INTERNAL_BASE_URL must be a valid URL')
    .transform((s) => s.replace(/\/$/, ''))
    .optional(),
  KEYCLOAK_REALM: z.string().default('demo'),
  KEYCLOAK_REDIRECT_URI: z.string().url('KEYCLOAK_REDIRECT_URI must be a valid URL'),
  KEYCLOAK_LOGOUT_URI: z.string().url('KEYCLOAK_LOGOUT_URI must be a valid URL').optional(),
  KEYCLOAK_SCOPES: z.string().default('openid profile email Agent.Invoke'),
  AI_AGENT_API_URL: z
    .string()
    .default('')
    .transform((s) => s.replace(/\/$/, '')),
  AI_AGENT_DNS_RETRY_ATTEMPTS: z.coerce.number().int().min(1).max(6).default(3),
  AI_AGENT_DNS_RETRY_BASE_DELAY_MS: z.coerce.number().int().min(10).max(5_000).default(150),
  AI_AGENT_DNS_RETRY_MAX_DELAY_MS: z.coerce.number().int().min(10).max(30_000).default(1_000),
  LOG_LEVEL: z
    .string()
    .default('info')
    .transform((s) => s.toLowerCase()),
  LOG_SERVICE_NAME: z.string().default('keycloak-vault-web-app'),
  LOG_ENVIRONMENT: z.string().default('development'),
  SESSION_PASSWORD: z
    .string()
    .min(32, 'SESSION_PASSWORD must be at least 32 characters'),
});

function parseConfig() {
  // During `next build`, route modules are imported to collect page data before
  // deploy-time env vars exist. Substitute placeholders so validation defers to
  // the runtime server process, which re-evaluates this file with real env.
  if (process.env.NEXT_PHASE === 'phase-production-build') {
    return schema.parse({
      ...process.env,
      KEYCLOAK_CLIENT_ID: process.env.KEYCLOAK_CLIENT_ID || 'build-placeholder',
      KEYCLOAK_CLIENT_SECRET: process.env.KEYCLOAK_CLIENT_SECRET || 'build-placeholder',
      KEYCLOAK_BASE_URL: process.env.KEYCLOAK_BASE_URL || 'https://build.placeholder',
      KEYCLOAK_REDIRECT_URI:
        process.env.KEYCLOAK_REDIRECT_URI || 'https://build.placeholder/callback',
      SESSION_PASSWORD: process.env.SESSION_PASSWORD || 'x'.repeat(32),
    });
  }
  const result = schema.safeParse(process.env);
  if (!result.success) {
    const issues = result.error.issues
      .map((i) => `  - ${i.path.join('.')}: ${i.message}`)
      .join('\n');
    throw new Error(`Invalid environment configuration:\n${issues}`);
  }
  return result.data;
}

export const config = parseConfig();

const publicRealmBase = `${config.KEYCLOAK_BASE_URL}/realms/${config.KEYCLOAK_REALM}/protocol/openid-connect`;
const internalRealmBase = `${config.KEYCLOAK_INTERNAL_BASE_URL || config.KEYCLOAK_BASE_URL}/realms/${config.KEYCLOAK_REALM}/protocol/openid-connect`;

export const oidc = {
  base: publicRealmBase,
  // Browser redirects — must be reachable from outside the cluster.
  authorizeUrl: `${publicRealmBase}/auth`,
  logoutUrl: `${publicRealmBase}/logout`,
  // Server-side calls — safe (and faster) to route over the mesh.
  tokenUrl: `${internalRealmBase}/token`,
  jwksUrl: `${internalRealmBase}/certs`,
} as const;

export const agent = {
  baseUrl: config.AI_AGENT_API_URL,
  queryUrl: config.AI_AGENT_API_URL ? `${config.AI_AGENT_API_URL}/v1/agent/query` : '',
  tokensUrl: config.AI_AGENT_API_URL ? `${config.AI_AGENT_API_URL}/v1/agent/tokens` : '',
  assuranceUrl: config.AI_AGENT_API_URL ? `${config.AI_AGENT_API_URL}/v1/agent/assurance` : '',
  retry: {
    maxAttempts: config.AI_AGENT_DNS_RETRY_ATTEMPTS,
    baseDelayMs: config.AI_AGENT_DNS_RETRY_BASE_DELAY_MS,
    maxDelayMs: config.AI_AGENT_DNS_RETRY_MAX_DELAY_MS,
  },
} as const;
