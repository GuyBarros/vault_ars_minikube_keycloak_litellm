import '@testing-library/jest-dom/vitest';

// Required env for any module that imports @/lib/config
process.env.KEYCLOAK_CLIENT_ID ||= 'test-client-id';
process.env.KEYCLOAK_CLIENT_SECRET ||= 'test-client-secret';
process.env.KEYCLOAK_BASE_URL ||= 'https://keycloak.example.com';
process.env.KEYCLOAK_REALM ||= 'demo';
process.env.KEYCLOAK_REDIRECT_URI ||= 'http://localhost:8080/api/auth/callback';
process.env.KEYCLOAK_SCOPES ||= 'openid profile email Agent.Invoke';
process.env.AI_AGENT_API_URL ||= 'https://agent.example.com';
process.env.LOG_LEVEL ||= 'silent';
process.env.LOG_SERVICE_NAME ||= 'keycloak-vault-web-app-test';
process.env.LOG_ENVIRONMENT ||= 'test';
process.env.SESSION_PASSWORD ||= 'a'.repeat(48);
