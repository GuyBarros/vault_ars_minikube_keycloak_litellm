package mcp.pep

import rego.v1

# REST decision for LiteLLM pdp_mcp.py:
#   POST /v1/data/mcp/pep/decision
#   input: {source, dest, tool, scope, user, groups, loa}
#
# Catalog is loaded from /vault/secrets/catalog.json (Vault KV
# opa-policies/mcp-authz/catalog) as data.catalog.

default decision := {
	"allow": false,
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
	"update_user_by_email": {"users.write"},
}

# Tools that need LoA 2: the user's token must carry acr >= 2, i.e. a Keycloak
# step-up login (password + OTP). `loa` is the token's acr as the PEP read it.
loa2_tools := {
	"create_user",
}

disabled_tools := {
	"delete_user_by_email",
}

current_loa := to_number(object.get(input, "loa", 1))

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
# CT-01.2: Autenticação Multifator / MFA (LoA=2) - PDP
decision := {
	"allow": false,
	"reason": "tool_disabled",
} if {
	input.tool in disabled_tools
}

decision := {
	"allow": true,
	"required_loa": 2,
	"reason": "loa2",
} if {
	tool_in_catalog
	scope_ok
	input.tool in loa2_tools
	current_loa >= 2
}

decision := {
	"allow": false,
	"step_up_required": true,
	"required_loa": 2,
	"reason": "step_up_required",
} if {
	not input.tool in disabled_tools
	tool_in_catalog
	scope_ok
	input.tool in loa2_tools
	current_loa < 2
}

decision := {
	"allow": true,
	"required_loa": 1,
	"reason": "allow",
} if {
	not input.tool in disabled_tools
	tool_in_catalog
	scope_ok
	not input.tool in loa2_tools
}

decision := {
	"allow": false,
	"reason": "insufficient_scope",
} if {
	not input.tool in disabled_tools
	tool_in_catalog
	not scope_ok
}

decision := {
	"allow": false,
	"reason": "catalog",
} if {
	not input.tool in disabled_tools
	input.tool
	not tool_in_catalog
}
