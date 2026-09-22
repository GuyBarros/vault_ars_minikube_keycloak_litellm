# Keycloak Setup

This project runs its own Keycloak instance in-cluster (no external tenant to
provision). Realm, clients, groups, and demo users are all defined in
[infra/local-minikube/templates/keycloak-realm.json](infra/local-minikube/templates/keycloak-realm.json)
and imported automatically when Keycloak boots (`start-dev --import-realm`).

`make keycloak` (see [infra/local-minikube/keycloak.sh](infra/local-minikube/keycloak.sh))
does everything: builds the custom Keycloak image (bakes in the
[keycloak-providers/](keycloak-providers/) SPI jar), generates client
secrets, renders and imports the realm, deploys Keycloak, and configures
Vault's `jwt-keycloak` auth mount + OAuth Resource Server + Agent Registry
against it. Re-running it is idempotent.

---

## Realm `demo`

| Client | Purpose |
|--------|---------|
| `web` | Browser OIDC login (Authorization Code + PKCE) |
| `litellm` | LiteLLM admin UI SSO (Authorization Code + PKCE) |
| `change` | RFC 8693 change broker used by `token-exchange` |
| `user-mcp` | Resource server — the audience for exchanged tokens |

Groups: `reader`, `writer`, `admin`. Demo logins: `user`/`user`
(reader), `writer`/`writer` (writer), `admin`/`admin` (admin).

Client secrets are generated once per cluster by `keycloak.sh` (stored under
`infra/local-minikube/generated/keycloak_client_secret_*`) and kept in sync
with `deploy-k8s/token-exchange.env`, `deploy-k8s/web-app.env`, and the
LiteLLM `GENERIC_CLIENT_SECRET`.

---

## The `keycloak-providers` SPI

Keycloak has no built-in way to (a) accept a Vault-issued actor token as an
RFC 8693 `actor_token`, or (b) emit RFC 9396 `authorization_details` so
Vault's OAuth Resource Server can enforce RAR. The custom protocol mapper in
[keycloak-providers/](keycloak-providers/) (`oidc-vault-jwt-compat-mapper`,
attached to the `token-exchange` client) fixes both:

- Strips the payload `typ` claim Keycloak always writes (Vault's OAuth
  Resource Server expects RFC 9068 `at+jwt`, header-only `typ`).
- Reads the Vault actor token from a custom `delegation_actor` form field
  (sent by `token-exchange/keycloak/keycloak_client.py`) and stamps an
  `act.sub` / `act.agent_id` claim from its `agent_id` claim.
- Synthesizes `authorization_details` (`vault:path_access` entries) from the
  `users.read`/`users.write` scopes on the request.

## Vault trust

`keycloak.sh` configures two Keycloak-facing things in Vault:

- `jwt-keycloak` JWT auth mount — used to validate a completed step-up (the
  write role's `bound_claims` require `acr: "2"`).
- `sys/config/oauth-resource-server/keycloak-demo` — Vault 2.1's native OAuth
  Resource Server, which validates the Keycloak OBO JWT presented directly
  as `X-Vault-Token` on `database/creds/...` and `transform/encode/...`,
  intersecting the human's baseline ACL with the `ai-agent` Agent Registry
  ceiling (`agent-registry/register`).

This needs a Vault Enterprise license with the Agentic IAM entitlement at
`infra/config/vault_license.hclic`.

---

## Debugging

```bash
# Realm discovery document
curl -s http://localhost:8081/realms/demo/.well-known/openid-configuration | jq .

# Vault's view of the OAuth Resource Server profile
VAULT_ADDR=https://localhost:8200 VAULT_SKIP_VERIFY=1 \
  vault read sys/config/oauth-resource-server/keycloak-demo
```

| Symptom | Likely cause |
|---------|--------------|
| `sys/config/oauth-resource-server` write fails | Vault license lacks the Agentic IAM entitlement, or Vault isn't 2.1+ |
| Browser login redirects to a URL that doesn't resolve | `KEYCLOAK_BASE_URL` (web-app.env) points at an in-cluster host instead of `localhost:8081` |
| `database/creds/...` 403s even for a legitimate reader | Check the Keycloak token's `groups`/`scope` claims and the matching `jwt-keycloak` role's `bound_claims` |
