interface Props {
  error?: string;
}

export function LoginPage({ error }: Props) {
  return (
    <div className="login-shell" data-carbon-theme="g100">
      <header className="login-header" role="banner">
        <span className="login-header__name">
          <span className="sub">AI Runtime Security</span>
        </span>
      </header>
      <main className="login-main">
        <div className="login-stack">
          <div className="login-eyebrow">Sign in</div>
          <h1 className="login-title">AI Runtime Security</h1>
          <p className="login-sub">
            Your AI. Your data. Sign in to start a governed conversation with your AI agent.
          </p>
          <a className="login-btn" href="/api/auth/login">
            <span>Login with Keycloak</span>
            <span className="login-btn__icon" aria-hidden="true">
              <svg viewBox="0 0 16 16" width="16" height="16" fill="none">
                <path
                  d="M3 8h10m-4-4 4 4-4 4"
                  stroke="currentColor"
                  strokeWidth="1.25"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </span>
          </a>
          {error ? (
            <div className="login-error" role="alert">
              {error}
            </div>
          ) : null}
          <div className="login-caption">
            Secured with Keycloak, IBM watsonx Governance, HashiCorp Consul &amp; Vault.
          </div>
        </div>
      </main>
    </div>
  );
}
