package mcp.authz_test

import rego.v1

import data.mcp.authz

trust_domain := "e288fbdf-9972-e8ab-7e1f-ba185939a0ca.consul"

# Standard pilot catalog: ai-agent → user-mcp with three allowed tools.
# Shape matches what Vault Agent renders at data.policy.mcp_authz.
pilot_catalog := {"rules": {"default/ai-agent": {"default/user-mcp": {"allow": [
	"list_all_users",
	"search_users_by_first_name",
	"update_user_by_email",
]}}}}

# Build an Envoy CheckRequest with Consul's filter metadata populated for
# source identity AND the destination's SPIFFE URI on input.attributes.destination.
req(src_ns, src_svc, dst_ns, dst_svc, body) := {"attributes": {
	"metadataContext": {"filterMetadata": {"consul": {
		"namespace": src_ns,
		"service": src_svc,
	}}},
	"destination": {"principal": sprintf(
		"spiffe://%s/ns/%s/dc/dc1/svc/%s",
		[trust_domain, dst_ns, dst_svc],
	)},
	"request": {"http": {
		"method": "POST",
		"path": "/mcp",
		"body": json.marshal(body),
	}},
}}

test_allowed_tool_call_list_all_users if {
	d := authz.decision with input as req("default", "ai-agent", "default", "user-mcp", {
		"jsonrpc": "2.0",
		"id": 1,
		"method": "tools/call",
		"params": {"name": "list_all_users"},
	}) with data.policy.mcp_authz as pilot_catalog
	d.allowed == true
}

test_allowed_tool_call_update_user if {
	d := authz.decision with input as req("default", "ai-agent", "default", "user-mcp", {
		"jsonrpc": "2.0",
		"id": 2,
		"method": "tools/call",
		"params": {"name": "update_user_by_email"},
	}) with data.policy.mcp_authz as pilot_catalog
	d.allowed == true
}

test_denied_tool_create_user if {
	d := authz.decision with input as req("default", "ai-agent", "default", "user-mcp", {
		"jsonrpc": "2.0",
		"id": 3,
		"method": "tools/call",
		"params": {"name": "create_user"},
	}) with data.policy.mcp_authz as pilot_catalog
	d.allowed == false
}

test_denied_tool_delete_user if {
	d := authz.decision with input as req("default", "ai-agent", "default", "user-mcp", {
		"jsonrpc": "2.0",
		"id": 4,
		"method": "tools/call",
		"params": {"name": "delete_user_by_email"},
	}) with data.policy.mcp_authz as pilot_catalog
	d.allowed == false
}

test_allowed_tools_list if {
	d := authz.decision with input as req("default", "ai-agent", "default", "user-mcp", {
		"jsonrpc": "2.0",
		"id": 5,
		"method": "tools/list",
	}) with data.policy.mcp_authz as pilot_catalog
	d.allowed == true
}

test_allowed_initialize if {
	d := authz.decision with input as req("default", "ai-agent", "default", "user-mcp", {
		"jsonrpc": "2.0",
		"id": 6,
		"method": "initialize",
	}) with data.policy.mcp_authz as pilot_catalog
	d.allowed == true
}

# An unrecognized source is still allowed to discover (the mesh let it in),
# but tools/call without a catalog entry must deny.
test_unknown_source_agent_denied_for_tools_call if {
	d := authz.decision with input as req("default", "random-svc", "default", "user-mcp", {
		"jsonrpc": "2.0",
		"id": 7,
		"method": "tools/call",
		"params": {"name": "list_all_users"},
	}) with data.policy.mcp_authz as pilot_catalog
	d.allowed == false
}

# Same source service in a different namespace is a different identity —
# tools/call must deny even though discovery would be allowed.
test_source_namespace_isolation_denied if {
	d := authz.decision with input as req("staging", "ai-agent", "default", "user-mcp", {
		"jsonrpc": "2.0",
		"id": 8,
		"method": "tools/call",
		"params": {"name": "list_all_users"},
	}) with data.policy.mcp_authz as pilot_catalog
	d.allowed == false
}

# Calling tools/call on a destination service that isn't in the catalog for
# this source.
test_unknown_destination_denied if {
	d := authz.decision with input as req("default", "ai-agent", "default", "payments-mcp", {
		"jsonrpc": "2.0",
		"id": 9,
		"method": "tools/call",
		"params": {"name": "list_all_users"},
	}) with data.policy.mcp_authz as pilot_catalog
	d.allowed == false
}

# Same destination service in a different namespace is a different identity.
test_destination_namespace_isolation_denied if {
	d := authz.decision with input as req("default", "ai-agent", "staging", "user-mcp", {
		"jsonrpc": "2.0",
		"id": 10,
		"method": "tools/call",
		"params": {"name": "list_all_users"},
	}) with data.policy.mcp_authz as pilot_catalog
	d.allowed == false
}

# Missing/empty catalog (data document never written) must fail closed for
# tools/call even when the source is mesh-authenticated.
test_empty_catalog_default_denies if {
	d := authz.decision with input as req("default", "ai-agent", "default", "user-mcp", {
		"jsonrpc": "2.0",
		"id": 11,
		"method": "tools/call",
		"params": {"name": "list_all_users"},
	}) with data.mcp.authz.catalog as {}
	d.allowed == false
}

# Discovery is gated by mesh authentication, not by catalog membership.
# A source with no catalog entry can still enumerate the tool surface.
test_discovery_allowed_without_catalog_entry if {
	d := authz.decision with input as req("default", "consul-mcp-authz", "default", "user-mcp", {
		"jsonrpc": "2.0",
		"id": 12,
		"method": "tools/list",
	}) with data.policy.mcp_authz as pilot_catalog
	d.allowed == true
}

test_missing_consul_metadata_denied if {
	d := authz.decision with input as {"attributes": {
		"destination": {"principal": sprintf("spiffe://%s/ns/default/dc/dc1/svc/user-mcp", [trust_domain])},
		"request": {"http": {"body": "{\"method\":\"tools/list\"}"}},
	}} with data.policy.mcp_authz as pilot_catalog
	d.allowed == false
}

test_malformed_body_denied if {
	d := authz.decision with input as {"attributes": {
		"metadataContext": {"filterMetadata": {"consul": {
			"namespace": "default",
			"service": "ai-agent",
		}}},
		"destination": {"principal": sprintf("spiffe://%s/ns/default/dc/dc1/svc/user-mcp", [trust_domain])},
		"request": {"http": {"body": "not-json"}},
	}} with data.policy.mcp_authz as pilot_catalog
	d.allowed == false
}
