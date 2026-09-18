# Vault policy + JWT auth role fragments for the opa-mcp-authz pilot AND
# the consul-mcp-authz P1 pilot.
#
# Two ways to apply these:
#   (a) Quick path (manual): translate the snippets below into `vault policy write`
#       and `vault write auth/k8s_jwt/role/...` commands.
#   (b) Terraform path: mirror the resource patterns in
#       infra/modules/consul-client-k8s/vault.tf (see vault_policy.opa and
#       vault_jwt_auth_backend_role.opa_server) and apply with `terraform apply`.

# ----- (a) Manual application -----
#
# Vault policy granting read on the mcp-authz catalog KV path:
#
#   cat <<EOF | vault policy write opa-mcp-authz -
#   path "opa-policies/data/mcp-authz/catalog" {
#     capabilities = ["read"]
#   }
#   EOF
#
# JWT auth role binding the opa-mcp-authz ServiceAccount in the default
# namespace to that policy:
#
#   vault write auth/k8s_jwt/role/opa-mcp-authz \
#     role_type="jwt" \
#     bound_audiences="https://kubernetes.default.svc" \
#     bound_subject="system:serviceaccount:default:opa-mcp-authz" \
#     claim_mappings="/kubernetes.io/namespace=namespace" \
#     token_explicit_max_ttl=0 \
#     token_period=1800 \
#     token_policies="default,opa-mcp-authz" \
#     token_type="service" \
#     user_claim="sub" \
#     user_claim_json_pointer=true

# ----- (b) Terraform fragments to add to infra/modules/consul-client-k8s/vault.tf -----

# resource "vault_policy" "opa_mcp_authz" {
#   name = "opa-mcp-authz"
#
#   policy = <<-EOT
#     path "${local.opa_kv_mount_path}/data/mcp-authz/catalog" {
#       capabilities = ["read"]
#     }
#   EOT
# }
#
# resource "vault_jwt_auth_backend_role" "opa_mcp_authz" {
#   backend         = vault_jwt_auth_backend.k8s.path
#   role_name       = "opa-mcp-authz"
#   role_type       = "jwt"
#   bound_audiences = ["https://kubernetes.default.svc"]
#   bound_subject   = "system:serviceaccount:default:opa-mcp-authz"
#
#   claim_mappings = {
#     "/kubernetes.io/namespace" = "namespace"
#   }
#
#   token_explicit_max_ttl  = 0
#   token_period            = 1800
#   token_policies          = ["default", vault_policy.opa_mcp_authz.name]
#   token_type              = "service"
#   user_claim              = "sub"
#   user_claim_json_pointer = true
# }

# ===== consul-mcp-authz (P1 pilot) =====
#
# The API workload writes the catalog (operator-managed), so its Vault
# policy needs `create` and `update` on the data path (not just `read`),
# plus `read` on the KV v2 metadata path so it can surface versioning to
# clients.
#
# The Terraform form lives at:
#   infra/modules/consul-client-k8s/vault.tf
#     - resource "vault_policy" "consul_mcp_authz"
#     - resource "vault_jwt_auth_backend_role" "consul_mcp_authz"
# Apply with `terraform apply` from the module's root.
#
# Equivalent manual form (when not using Terraform):
#
#   cat <<EOF | vault policy write consul-mcp-authz -
#   path "opa-policies/data/mcp-authz/catalog" {
#     capabilities = ["create", "read", "update"]
#   }
#   path "opa-policies/metadata/mcp-authz/catalog" {
#     capabilities = ["read"]
#   }
#   EOF
#
#   vault write auth/k8s_jwt/role/consul-mcp-authz \
#     role_type="jwt" \
#     bound_audiences="https://kubernetes.default.svc" \
#     bound_subject="system:serviceaccount:default:consul-mcp-authz" \
#     claim_mappings="/kubernetes.io/namespace=namespace" \
#     token_explicit_max_ttl=0 \
#     token_period=1800 \
#     token_policies="default,consul-mcp-authz" \
#     token_type="service" \
#     user_claim="sub" \
#     user_claim_json_pointer=true
