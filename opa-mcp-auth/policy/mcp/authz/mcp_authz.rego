package mcp.authz

import rego.v1

# ---------------------------------------------------------------------------
# Decision document consumed by OPA's Envoy ext_authz gRPC plugin.
# Configured path: mcp/authz/decision  (-> data.mcp.authz.decision)
#
# Catalog is data-driven: rendered by Vault Agent into /vault/secrets and
# loaded by OPA under data.mcp.authz.catalog. The .rego file is constant;
# operators change behavior by writing to Vault KV v2.
# ---------------------------------------------------------------------------

default decision := {
	"allowed": false,
	"http_status": 403,
	"headers": {"x-authz-reason": "default-deny"},
}

decision := {
	"allowed": true,
	"headers": {
		"x-authz-agent": agent_service,
		"x-authz-dest": dest_service,
		"x-authz-method": method,
	},
} if allow

decision := {
	"allowed": false,
	"http_status": 403,
	"headers": {"x-authz-reason": sprintf(
		"agent=%v dest=%v method=%v tool=%v not permitted",
		[
			_or_dash(agent_service),
			_or_dash(dest_service),
			object.get(request_body, "method", "-"),
			object.get(object.get(request_body, "params", {}), "name", "-"),
		],
	)},
} if not allow

# ---------------------------------------------------------------------------
# Inputs extracted from the Envoy CheckRequest.
# ---------------------------------------------------------------------------

# Consul's ext_authz extension injects the SOURCE workload's identity into
# metadataContext.filterMetadata.consul. Use it directly; no SPIFFE parsing.
default consul_metadata := {}

consul_metadata := input.attributes.metadataContext.filterMetadata.consul

# Agent identifier: "<namespace>/<service>".
agent_service := sprintf("%s/%s", [ns, svc]) if {
	svc := consul_metadata.service
	ns := consul_metadata.namespace
}

# Destination identifier comes from parsing the local mTLS cert URI
# (input.attributes.destination.principal). Consul does NOT put destination
# fields into filterMetadata.consul — only source identity is there.
# SPIFFE shape: spiffe://<trust-domain>/ns/<ns>/dc/<dc>/svc/<svc>
default dest_service := ""

dest_service := sprintf("%s/%s", [ns, svc]) if {
	uri := input.attributes.destination.principal
	after_ns := split(uri, "/ns/")[1]
	ns := split(after_ns, "/")[0]
	svc := split(uri, "/svc/")[1]
}

# JSON-RPC body buffered by ext_authz (withRequestBody, packAsBytes=false).
default request_body := {}

request_body := json.unmarshal(input.attributes.request.http.body)

method := request_body.method

tool_name := request_body.params.name

# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

# MCP handshake and surface-enumeration methods. By the time OPA sees the
# request, Envoy has verified the mTLS peer cert and Consul's ServiceIntention
# has already gated reachability — that IS the authorization for "may A talk
# to B at all". The catalog's job is the finer-grained "which tools may A
# invoke on B", so discovery doesn't need a per-pair catalog entry.
discovery_methods := {
	"initialize",
	"notifications/initialized",
	"ping",
	"tools/list",
	"resources/list",
	"prompts/list",
}

# Methods that actually return content. They reuse the catalog membership
# check so an MCP server can host both publicly-listable surfaces and
# per-source-gated reads.
catalog_gated_methods := {
	"resources/read",
	"prompts/get",
}

# Catalog loaded from Vault Agent's rendered file. Vault Agent's template
# wraps the KV payload as data.policy.mcp_authz so it does not collide with
# this package (data.mcp.authz). Shape of the data document:
#   {"rules": {"<src-ns>/<src-svc>": {"<dst-ns>/<dst-svc>": {"allow": [...]}}}}
# A missing data document yields an empty catalog → default-deny for
# tools/call and catalog-gated methods.
default catalog := {}

catalog := data.policy.mcp_authz.rules

# A request is "known" when (source, destination) pair has an entry in the
# catalog. Tool-level allow/deny is then decided by the allow list.
known_pair if catalog[agent_service][dest_service]

# Discovery requires only that the mesh authenticated the source.
allow if {
	agent_service != ""
	method in discovery_methods
}

allow if {
	known_pair
	method in catalog_gated_methods
}

allow if {
	known_pair
	method == "tools/call"
	tool_name in catalog[agent_service][dest_service].allow
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_or_dash(s) := s if s != ""

_or_dash(s) := "-" if s == ""
