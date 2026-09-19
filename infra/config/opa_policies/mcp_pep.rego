package mcp.pep

import rego.v1

# REST decision for LiteLLM pdp_mcp.py:
#   POST /v1/data/mcp/pep/decision
#   input: {source, dest, tool, scope, user, groups}
#
# Catalog is loaded from /vault/secrets/catalog.json (Vault KV
# opa-policies/mcp-authz/catalog) as data.catalog.

default decision := {
	"allow": false,
	"ciba_required": false,
	"reason": "default-deny",
}

# catalog.json is {"rules": {...}}. OPA run /vault/secrets loads that JSON
# at data.rules. Do not reference `data` as a whole — that includes this
# package and OPA reports a recursion error.
mcp_rules := data.rules

required_scopes := {
	"list_all_users": {"users.read"},
	"search_users_by_first_name": {"users.read"},
	"create_user": {"users.write"},
	"delete_user_by_email": {"users.write"},
	"update_user_by_email": {"users.write"},
}

# Mirrors Vault policy ciba-write: create/delete need HITL; update is silent.
ciba_tools := {
	"create_user",
	"delete_user_by_email",
}

granted_scopes := {s |
	some s in split(object.get(input, "scope", ""), " ")
	s != ""
}

tool_in_catalog if {
	input.tool in mcp_rules[input.source][input.dest].allow
}

scope_ok if {
	required := required_scopes[input.tool]
	count(required - granted_scopes) == 0
}

decision := {
	"allow": true,
	"ciba_required": true,
	"reason": "step-up",
} if {
	tool_in_catalog
	scope_ok
	input.tool in ciba_tools
}

decision := {
	"allow": true,
	"ciba_required": false,
	"reason": "allow",
} if {
	tool_in_catalog
	scope_ok
	not input.tool in ciba_tools
}

decision := {
	"allow": false,
	"ciba_required": false,
	"reason": "insufficient_scope",
} if {
	tool_in_catalog
	not scope_ok
}

decision := {
	"allow": false,
	"ciba_required": false,
	"reason": "catalog",
} if {
	input.tool
	not tool_in_catalog
}
