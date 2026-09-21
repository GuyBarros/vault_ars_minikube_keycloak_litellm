# Build/destroy the ai-iam-guardrails demo on a fully local minikube (no AWS).
# See README.md for what each stage actually does and why.
#
#   make up        - full build: cluster -> control plane -> Keycloak -> images -> apps
#   make down      - destroy the minikube node (wipes everything)
#   make status    - pod status + direct-access check
#   make redeploy  - re-apply k8s manifests + local images (after an app-source
#                    or deploy-k8s/*.yaml change; doesn't touch Vault/Consul/Keycloak)

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

PROFILE      := local-minikube-demo
REPO_ROOT    := ../..
DEPLOY_DIR   := $(REPO_ROOT)/deploy-k8s
KC           := kubectl --context $(PROFILE)

.PHONY: up down status verify credentials bootstrap configure keycloak images deploy redeploy hop-logs help

help:
	@echo "make up        - full build (cluster, control plane, Keycloak, images, apps)"
	@echo "make down      - destroy the minikube node"
	@echo "make status    - pod status + direct-access check"
	@echo "make redeploy  - re-apply manifests + local images only"
	@echo "make hop-logs   - viewer ao vivo (kubectl logs --follow → http://127.0.0.1:8753/)"

up: bootstrap configure keycloak images deploy verify credentials

down:
	-docker rm -f litellm-ui-nodeport >/dev/null 2>&1
	minikube -p $(PROFILE) delete

bootstrap:
	./bootstrap.sh

configure:
	./configure.sh

# Builds the custom Keycloak image (keycloak-providers/ SPI baked in),
# deploys it, imports the demo realm, and configures Vault's jwt-keycloak
# auth mount + OAuth Resource Server + Agent Registry against it. Must run
# after configure.sh (needs the shared postgres-0 StatefulSet).
keycloak:
	./keycloak.sh

# Builds arm64 images from source for the 3 services Docker Hub only
# publishes amd64 builds for, plus token-exchange (its Docker Hub image is
# fine arch-wise, but this repo's local source is the one under active
# development and routinely drifts ahead of it - see the "keycloak wip"
# groups-claim rename in token-exchange/keycloak/authorization.py) and the
# local-only ciba-channel image, and loads them into the node. See README.md.
images:
	[ -f $(REPO_ROOT)/user-mcp/users_repository.json ] || \
		cp $(REPO_ROOT)/infra/modules/minikube-resources-config/users_seed.json \
		   $(REPO_ROOT)/user-mcp/users_repository.json
	docker build --load -t agentguard-user-mcp:local $(REPO_ROOT)/user-mcp
	docker build --load -t agentguard-ai-agent:local $(REPO_ROOT)/ai-agent
	docker build --load -t agentguard-web-app:local $(REPO_ROOT)/web-app
	docker build --load -t agentguard-token-exchange:local $(REPO_ROOT)/token-exchange
	docker build --load -t ciba-channel:local $(REPO_ROOT)/ciba-channel
	docker build --load -t agentguard-opa-gov-api:local $(REPO_ROOT)/opa-gov-api
	minikube -p $(PROFILE) image load agentguard-user-mcp:local
	minikube -p $(PROFILE) image load agentguard-ai-agent:local
	minikube -p $(PROFILE) image load agentguard-web-app:local
	minikube -p $(PROFILE) image load agentguard-token-exchange:local
	minikube -p $(PROFILE) image load ciba-channel:local
	minikube -p $(PROFILE) image load agentguard-opa-gov-api:local

deploy:
	# service-intentions.yaml carries opa-service's intention in the `opa` namespace;
	# without the namespace that one CR fails (masked by the `|| true` below).
	$(KC) create namespace opa --dry-run=client -o yaml | $(KC) apply -f -
	$(KC) apply -f $(DEPLOY_DIR)/proxy-defaults.yaml
	$(KC) apply -f $(DEPLOY_DIR)/mesh.yaml
	$(KC) apply -f mesh-vault-postgres.yaml || true
	-./ensure-litellm-pep-vault-role.sh
	$(KC) apply -f $(DEPLOY_DIR)/service-intentions.yaml || true
	$(KC) create secret generic token-exchange-env --from-file=.env=$(DEPLOY_DIR)/token-exchange.env --dry-run=client -o yaml | $(KC) apply -f -
	$(KC) apply -f $(DEPLOY_DIR)/token-exchange.yaml
	$(KC) create secret generic user-mcp-env --from-file=.env=$(DEPLOY_DIR)/user-mcp.env --dry-run=client -o yaml | $(KC) apply -f -
	$(KC) apply -f $(DEPLOY_DIR)/user-mcp.yaml
	[ -f generated/litellm_master_key ] || openssl rand -hex 24 > generated/litellm_master_key
	[ -f generated/litellm_sso_state ] || openssl rand -hex 24 > generated/litellm_sso_state
	[ -f generated/keycloak_client_secret_litellm ] || { echo "missing generated/keycloak_client_secret_litellm (run make keycloak)"; exit 1; }
	[ -f generated/litellm_db_password ] || { echo "missing generated/litellm_db_password (run make keycloak)"; exit 1; }
	[ -f generated/keycloak_client_secret_ciba ] || { echo "missing generated/keycloak_client_secret_ciba (run make keycloak)"; exit 1; }
	$(KC) create secret generic litellm-gateway-env \
	  --from-literal=LITELLM_MASTER_KEY=$$(cat generated/litellm_master_key) \
	  --from-literal=OPENAI_API_KEY=$${OPENAI_API_KEY:-} \
	  --from-literal=GENERIC_CLIENT_SECRET=$$(cat generated/keycloak_client_secret_litellm) \
	  --from-literal=GENERIC_CLIENT_STATE=$$(cat generated/litellm_sso_state) \
	  --from-literal=DATABASE_URL=postgresql://litellm_user:$$(cat generated/litellm_db_password)@postgres.default.svc.cluster.local:5432/litellm \
	  --from-literal=PEP_OPA_URL=http://opa-service.opa.svc.cluster.local \
	  --from-literal=PEP_OPA_DECISION_PATH=/v1/data/mcp/pep/decision \
	  --from-literal=PEP_CIBA_KEYCLOAK_URL=http://keycloak.virtual.consul \
	  --from-literal=PEP_CIBA_REALM=demo \
	  --from-literal=PEP_CIBA_CLIENT_ID=ciba-client \
	  --from-literal=PEP_CIBA_CLIENT_SECRET=$$(cat generated/keycloak_client_secret_ciba) \
	  --from-literal=PEP_CIBA_POLL_TIMEOUT_SECONDS=110 \
	  --from-literal=PEP_CIBA_APPROVE_URL=http://localhost:8093 \
	  --from-literal=PEP_MCP_SOURCE=default/litellm-gateway \
	  --from-literal=PEP_MCP_DEST=default/user-mcp \
	  --from-literal=PEP_MCP_AUDIENCE=user-mcp \
	  --from-literal=PEP_MCP_ISSUER=http://localhost:8081/realms/demo \
	  --from-literal=PEP_SUBJECT_ISSUER=http://localhost:8081/realms/demo \
	  --from-literal=PEP_SUBJECT_AUDIENCE=token-exchange \
	  --from-literal=PEP_KEYCLOAK_JWKS_URL=http://keycloak.virtual.consul/realms/demo/protocol/openid-connect/certs \
	  --dry-run=client -o yaml | $(KC) apply -f -
	$(KC) create configmap litellm-gateway-config \
	  --from-file=config.yaml=$(REPO_ROOT)/litellm-gateway/config.yaml \
	  --from-file=pdp_auth.py=$(REPO_ROOT)/litellm-gateway/pdp_auth.py \
	  --from-file=pdp_guardrail.py=$(REPO_ROOT)/litellm-gateway/pdp_guardrail.py \
	  --from-file=pdp_mcp.py=$(REPO_ROOT)/litellm-gateway/pdp_mcp.py \
	  --dry-run=client -o yaml | $(KC) apply -f -
	$(KC) apply -f $(DEPLOY_DIR)/litellm-gateway.yaml
	$(KC) apply -f $(DEPLOY_DIR)/litellm-gateway-gateway.yaml
	# OPA content PDP: LiteLLM guardrail -> opa-gov-api -> opa-service (policies from
	# Vault KV opa-policies/bundle). Set here, not in litellm-gateway.yaml: that manifest
	# is shared, and the guardrail denies every prompt when its OPA is unreachable.
	$(KC) apply -n opa -f $(DEPLOY_DIR)/opa-server.yaml
	$(KC) apply -f $(DEPLOY_DIR)/opa-gov-api.yaml
	# Consul virtual address of opa-gov-api; the port is its Service port.
	$(KC) set env deployment/litellm-gateway OPA_GOV_API_URL=http://opa-gov-api.virtual.consul:8000
	$(KC) create secret generic ai-agent-env --from-file=.env=$(DEPLOY_DIR)/ai-agent.env --dry-run=client -o yaml | $(KC) apply -f -
	$(KC) apply -f $(DEPLOY_DIR)/ai-agent.yaml
	$(KC) apply -f $(DEPLOY_DIR)/ai-agent-config.yaml
	$(KC) apply -f $(DEPLOY_DIR)/ciba-channel.yaml
	$(KC) apply -f $(DEPLOY_DIR)/service-defaults-ciba-channel.yaml
	$(KC) apply -f $(DEPLOY_DIR)/ciba-channel-gateway.yaml
	# Catalog is now enforced by LiteLLM (pdp_mcp.py). opa-mcp-authz stays
	# deployed so consul-mcp-authz can still manage the Vault KV catalog.
	$(KC) create configmap opa-mcp-authz-policy \
	  --from-file=mcp_authz.rego=$(REPO_ROOT)/opa-mcp-auth/policy/mcp/authz/mcp_authz.rego \
	  --dry-run=client -o yaml | $(KC) apply -f -
	$(KC) apply -f $(DEPLOY_DIR)/opa-mcp-authz.yaml
	$(KC) apply -f $(DEPLOY_DIR)/service-defaults-opa-mcp-authz.yaml
	$(KC) apply -f $(DEPLOY_DIR)/service-defaults-user-mcp.yaml
	$(KC) create secret generic web-env --from-file=.env=$(DEPLOY_DIR)/web-app.env --dry-run=client -o yaml | $(KC) apply -f -
	$(KC) apply -f $(DEPLOY_DIR)/web-app.yaml
	$(KC) apply -f $(DEPLOY_DIR)/web-app-gateway.yaml
	# Applied last: its HTTPRoute/web-route re-declaration (with the
	# RouteTimeoutFilter attached) must win over web-app-gateway.yaml's plain
	# version above, and kubectl apply resolves same-name conflicts by
	# last-applied-wins — see mesh-timeouts.yaml's header comment.
	$(KC) apply -f mesh-timeouts.yaml
	$(KC) set image deployment/user-mcp user-mcp=agentguard-user-mcp:local
	$(KC) set image deployment/ai-agent ai-agent=agentguard-ai-agent:local
	$(KC) set image deployment/web web=agentguard-web-app:local
	$(KC) set image deployment/token-exchange token-exchange=agentguard-token-exchange:local
	$(KC) set image deployment/opa-gov-api api=agentguard-opa-gov-api:local
	$(KC) patch deployment user-mcp -n default --type=json -p='[{"op":"replace","path":"/spec/template/spec/containers/0/imagePullPolicy","value":"Never"}]'
	$(KC) patch deployment ai-agent -n default --type=json -p='[{"op":"replace","path":"/spec/template/spec/containers/0/imagePullPolicy","value":"Never"}]'
	$(KC) patch deployment web -n default --type=json -p='[{"op":"replace","path":"/spec/template/spec/containers/0/imagePullPolicy","value":"Never"}]'
	$(KC) patch deployment token-exchange -n default --type=json -p='[{"op":"replace","path":"/spec/template/spec/containers/0/imagePullPolicy","value":"Never"}]'
	$(KC) patch deployment opa-gov-api -n default --type=json -p='[{"op":"replace","path":"/spec/template/spec/containers/0/imagePullPolicy","value":"Never"}]'
	# Consul creates each *-api-gateway Service asynchronously; patching NodePort
	# before it exists would create a ClusterIP Service that rejects nodePort.
	@for svc in web-api-gateway ciba-channel-gateway litellm-api-gateway; do \
	  for i in $$(seq 1 60); do \
	    $(KC) get svc $$svc >/dev/null 2>&1 && break; \
	    sleep 2; \
	  done; \
	done
	$(KC) apply -f web-nodeport.yaml
	$(KC) apply -f ciba-channel-nodeport.yaml
	$(KC) apply -f litellm-gateway-nodeport.yaml
	# Host :4000 is published at minikube create (--ports 4000:30083). Probe
	# TCP listen, not /health: LiteLLM is often still starting here, and a
	# 503 used to look like "port missing" then docker bind failed because
	# minikube already owns :4000. Bridge only when nothing is listening.
	@if ! (echo >/dev/tcp/127.0.0.1/4000) >/dev/null 2>&1; then \
	  echo "host :4000 not published on this minikube node; bridging NodePort 30083"; \
	  docker rm -f litellm-ui-nodeport >/dev/null 2>&1 || true; \
	  docker run -d --name litellm-ui-nodeport --restart unless-stopped -p 4000:80 alpine/socat \
	    TCP-LISTEN:80,fork,reuseaddr TCP:$$(minikube -p $(PROFILE) ip):30083 >/dev/null \
	    || echo "could not bind host :4000; LiteLLM UI at $$(minikube -p $(PROFILE) ip):30083"; \
	fi
	$(KC) rollout status deployment/litellm-gateway --timeout=180s
	$(KC) rollout status deployment/ai-agent --timeout=180s
	$(KC) rollout status deployment/web --timeout=180s
	$(KC) rollout status deployment/user-mcp --timeout=180s

# For iterating on app source or deploy-k8s/*.yaml without redoing the
# cluster/control-plane stages.
redeploy: images deploy verify credentials

verify:
	@echo "--- pods ---"
	$(KC) get pods -n default
	@echo "--- direct access (no port-forward) ---"
	curl -s -o /dev/null -w "web      (localhost:8080): HTTP %{http_code}\n" http://localhost:8080/
	curl -s -o /dev/null -w "keycloak (localhost:8081): HTTP %{http_code}\n" http://localhost:8081/realms/demo/.well-known/openid-configuration
	curl -s -o /dev/null -w "ciba     (localhost:8082): HTTP %{http_code}\n" http://localhost:8082/
	curl -s -o /dev/null -w "litellm  (localhost:4000): HTTP %{http_code}\n" http://localhost:4000/ui
	curl -sk -o /dev/null -w "vault    (localhost:8200): HTTP %{http_code}\n" https://localhost:8200/v1/sys/health
	curl -sk -o /dev/null -w "consul   (localhost:8501): HTTP %{http_code}\n" https://localhost:8501/v1/status/leader

credentials:
	./print-credentials.sh

hop-logs:
	@-lsof -tiTCP:8753 -sTCP:LISTEN | xargs kill -9 >/dev/null 2>&1 || true
	@sleep 0.3
	cd $(REPO_ROOT)/vault-log && python3 serve.py >/tmp/vault-log-serve.out 2>&1 &
	@for i in 1 2 3 4 5 6 7 8 9 10 11 12; do \
	  curl -sf http://127.0.0.1:8753/health >/dev/null && break; \
	  sleep 0.4; \
	done
	@echo "hop viewer ao vivo: http://127.0.0.1:8753/"
	-open http://127.0.0.1:8753/

status: verify
