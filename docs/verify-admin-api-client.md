# Creating the IBM Verify Admin API Client

`setup_verify.py` authenticates to IBM Verify using a **confidential API client**
(client credentials grant). This document explains how to create that client and
which entitlements it requires.

---

## What the admin client is used for

The client is used **only** by `setup_verify.py` and `make jwks-push` — never by
the running application stack. It needs enough permission to:

| Action | API endpoint | Entitlement required |
|--------|-------------|----------------------|
| List existing OIDC apps | `GET /v1.0/applications` | **Manage applications** (`manageApps`) |
| Create / update OIDC apps | `POST /appaccess/v1.0/applications`, `PUT …/{id}` | **Manage applications** (`manageApps`) |
| List / upload signer certificates | `GET /v1.0/signercert`, `POST /v1.0/signercert` | **Manage certificates** (`manageCerts`) |
| Read / update the `vaultjwt` STS token type | `GET /oidc-mgmt/v1.0/sts/tokentypes/vaultjwt`, `PUT …` | **Manage STS clients** (`manageSTSClients`) |

---

## Step-by-step: create the client

1. **Sign in** to your IBM Verify admin console:
   `https://<tenant>.verify.ibm.com`

2. Navigate to **Security → API Access → API clients**.

3. Click **Add API client**.

4. **Name** the client something recognisable, e.g. `ai-iam-guardrails-admin`.
   Description is optional.

5. **Select entitlements.** Enable all three listed below — the console shows
   them as a searchable checklist on the same page.

   | Entitlement label | Internal name | What it unlocks |
   |-------------------|---------------|-----------------|
   | Manage applications | `manageApps` | Create, read, and update OIDC application registrations |
   | Manage certificates | `manageCerts` | Upload and list signer certificates (used for Vault JWKS) |
   | Manage STS clients | `manageSTSClients` | Read and update STS custom token types (`vaultjwt`) |

   > **Tip — finding entitlements in the UI.** The entitlement list is long.
   > Type "manage app", "manage cert", or "manage sts" in the search box to
   > jump straight to the relevant rows.

6. Click **Save**.

7. IBM Verify generates a **Client ID** and **Client Secret**. Copy both
   immediately — the secret is only shown once.

---

## Store the credentials

Create `scripts/.env` (this file is git-ignored):

```bash
export VERIFY_TENANT_URL=https://<tenant>.verify.ibm.com
export VERIFY_ADMIN_CLIENT_ID=<client_id_from_step_7>
export VERIFY_ADMIN_CLIENT_SECRET=<client_secret_from_step_7>
```

`setup_verify.py` and the Makefile `source` this file automatically:

```bash
# Full IBM Verify setup (first time or new tenant)
python3 scripts/setup_verify.py

# Re-sync Vault signing keys only (after every cluster rebuild)
make jwks-push
```

---

## Checking what entitlements an existing client has

If you have an existing API client and are unsure whether it has the right
entitlements:

1. Go to **Security → API Access → API clients**.
2. Click the client name.
3. The **Entitlements** tab lists all currently granted permissions.

Alternatively, use the API itself:

```bash
# Get an access token for the client
token=$(curl -s -X POST "$VERIFY_TENANT_URL/oauth2/token" \
  --data-urlencode "grant_type=client_credentials" \
  --data-urlencode "client_id=$VERIFY_ADMIN_CLIENT_ID" \
  --data-urlencode "client_secret=$VERIFY_ADMIN_CLIENT_SECRET" \
  --data-urlencode "scope=openid" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Try each of the three API calls setup_verify.py makes:

# 1. manageApps — list applications
curl -s -o /dev/null -w "GET /v1.0/applications → HTTP %{http_code}\n" \
  -H "Authorization: Bearer $token" "$VERIFY_TENANT_URL/v1.0/applications"

# 2. manageCerts — list signer certs
curl -s -o /dev/null -w "GET /v1.0/signercert → HTTP %{http_code}\n" \
  -H "Authorization: Bearer $token" "$VERIFY_TENANT_URL/v1.0/signercert"

# 3. manageSTSClients — read the vaultjwt STS token type
curl -s -o /dev/null -w "GET /oidc-mgmt/v1.0/sts/tokentypes/vaultjwt → HTTP %{http_code}\n" \
  -H "Authorization: Bearer $token" "$VERIFY_TENANT_URL/oidc-mgmt/v1.0/sts/tokentypes/vaultjwt"
```

Expected: all three return `200`. A `403` means the entitlement is missing for
that call; a `404` on the STS route means the `vaultjwt` custom token type has
not been created yet (see [IBM_VERIFY_OIDC_APPS.md](../IBM_VERIFY_OIDC_APPS.md#sts-token-type--vaultjwt)).

---

## What this client does NOT need

Keep the client's permissions minimal. It does **not** need:

- Manage users, groups, or roles
- Manage MFA / authenticators
- Read or write audit logs
- Any entitlements related to identity sources or SCIM provisioning
- Directory management

---

## Rotating the client secret

IBM Verify does not expire API client secrets automatically, but you should
rotate them if they are ever exposed.

1. Go to **Security → API Access → API clients → `ai-iam-guardrails-admin`**.
2. Click **Regenerate secret** (or equivalent for your tenant version).
3. Update `scripts/.env` with the new value.
4. `setup_verify.py` and `make jwks-push` will use the new secret on next run.
