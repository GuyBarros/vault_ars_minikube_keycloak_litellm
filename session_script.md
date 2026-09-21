# CT-01.1:
 login no web app
# CT-01.2:
 crie um usuario com o usuario admin
# CT-01.3:
 skipped
# CT-01.4:
 login with wrong user
# CT-01.5: Validação da Integridade e Assinatura do JWT no PEP
## 1 Break the Service Mesh and to call services directly
```
    Port-forward the ai-agent Service (same as the demo script does)
    kubectl --context local-minikube-demo port-forward deploy/ai-agent 18000:8000 &
```
## 2. Call it, asking the agent to create a user
```
JWT='eyJhbGciOiJSUzI1NiIsInR5cCIgOiAiYXQrand0Iiwia2lkIiA6ICJaVHRCRmE3a2NIVUc3Rm1SSTVwaDdWNHl3dGJGSnVmbm5TcnpCTHJjNWtnIn0.eyJleHAiOjE3OTAwMjMyOTIsImlhdCI6MTc4OTk5NDQ5MiwianRpIjoibnRydHRlOjYyNWRkNTVkLTkyODItYmM5Ni03NzBiLWQ3N2M2MjFiYzMxYyIsImlzcyI6Imh0dHA6Ly9sb2NhbGhvc3Q6ODA4MS9yZWFsbXMvZGVtbyIsImF1ZCI6InVzZXItbWNwIiwic3ViIjoiYWRtaW4iLCJhenAiOiJ0b2tlbi1leGNoYW5nZSIsInNpZCI6IjFxVGJkU2dDWUwzMnJDd2hEX0ttU2VHRCIsInNjb3BlIjoidXNlcnMud3JpdGUiLCJhY3QiOnsic3ViIjoiYWktYWdlbnQiLCJhZ2VudF9pZCI6ImFpLWFnZW50IiwiaXNzIjoiaHR0cHM6Ly92YXVsdC52YXVsdC5zdmMuY2x1c3Rlci5sb2NhbDo4MjAwL3YxL2lkZW50aXR5L29pZGMifSwiYXV0aG9yaXphdGlvbl9kZXRhaWxzIjpbeyJ0eXBlIjoidmF1bHQ6cGF0aF9hY2Nlc3MiLCJwYXRoIjoiZGF0YWJhc2UvY3JlZHMvdXNlci1tY3Atd3JpdGUtcm9sZSIsImNhcGFiaWxpdGllcyI6WyJyZWFkIl19LHsidHlwZSI6InZhdWx0OnBhdGhfYWNjZXNzIiwicGF0aCI6InRyYW5zZm9ybS9lbmNvZGUvdXNlci1tY3AtdHJhbnNmb3JtIiwiY2FwYWJpbGl0aWVzIjpbImNyZWF0ZSIsInVwZGF0ZSJdfSx7InR5cGUiOiJ2YXVsdDpwYXRoX2FjY2VzcyIsInBhdGgiOiJzeXMvbGVhc2VzL3Jldm9rZSIsImNhcGFiaWxpdGllcyI6WyJ1cGRhdGUiXX1dLCJncm91cHMiOlsiYWRtaW4iXSwicHJlZmVycmVkX3VzZXJuYW1lIjoiYWRtaW4ifQ.WloZ_wpCeqg6cpNs0KclmH4tck0Prz0kYd2ezUdHKE6kkntF4nEoKaokvwuOsgPKrY3-SAzBd0XLq2LfN7dQeHEQdyRP1opVirsAO0WeuL10ntRSITFZWqtF_OEYjK2km_xeeY8bbhkjsGZgk6rRm4fJ-26zf2QkZP-SPK8WvQam_9FtuFKh-8aukcH-KxI3bacRjxl0bngjhkIT26Bcd5qhFXQ8YmQBxFyjzvhcfpRDZAewvHMT-BwcKsFaHu33GeN8WtZlDDCsOasxDhpD3IAQ--ISkWXq6XXOZ6_-sPPdk_KkNHw1HnOx81jGFd2K_VyMC9Z1FRpCoqrnVgSOqw'

curl -s -X POST http://localhost:18000/v1/agent/query \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "Create a new user: first name Test, last name Curl, email test.curl@example.test"}
    ]
  }'
```

# CT-02.01: 
kubectl --context local-minikube-demo logs deploy/litellm-gateway -c litellm-gateway | grep admit_mesh

# CT-02.2: 
kubectl --context local-minikube-demo patch serviceintentions litellm-gateway \
  --type=json \
  -p '[{"op":"replace","path":"/spec/sources/1/action","value":"deny"}]'

kubectl --context local-minikube-demo get serviceintentions litellm-gateway

kubectl --context local-minikube-demo patch serviceintentions litellm-gateway \
  --type=json \
  -p '[{"op":"replace","path":"/spec/sources/1/action","value":"allow"}]'

CT-02.3: pegar um JWT antigo
  JWT='eyJhbGciOiJSUzI1NiIsInR5cCIgOiAiYXQrand0Iiwia2lkIiA6ICJaVHRCRmE3a2NIVUc3Rm1SSTVwaDdWNHl3dGJGSnVmbm5TcnpCTHJjNWtnIn0.eyJleHAiOjE3OTAwMjMyOTIsImlhdCI6MTc4OTk5NDQ5MiwianRpIjoibnRydHRlOjYyNWRkNTVkLTkyODItYmM5Ni03NzBiLWQ3N2M2MjFiYzMxYyIsImlzcyI6Imh0dHA6Ly9sb2NhbGhvc3Q6ODA4MS9yZWFsbXMvZGVtbyIsImF1ZCI6InVzZXItbWNwIiwic3ViIjoiYWRtaW4iLCJhenAiOiJ0b2tlbi1leGNoYW5nZSIsInNpZCI6IjFxVGJkU2dDWUwzMnJDd2hEX0ttU2VHRCIsInNjb3BlIjoidXNlcnMud3JpdGUiLCJhY3QiOnsic3ViIjoiYWktYWdlbnQiLCJhZ2VudF9pZCI6ImFpLWFnZW50IiwiaXNzIjoiaHR0cHM6Ly92YXVsdC52YXVsdC5zdmMuY2x1c3Rlci5sb2NhbDo4MjAwL3YxL2lkZW50aXR5L29pZGMifSwiYXV0aG9yaXphdGlvbl9kZXRhaWxzIjpbeyJ0eXBlIjoidmF1bHQ6cGF0aF9hY2Nlc3MiLCJwYXRoIjoiZGF0YWJhc2UvY3JlZHMvdXNlci1tY3Atd3JpdGUtcm9sZSIsImNhcGFiaWxpdGllcyI6WyJyZWFkIl19LHsidHlwZSI6InZhdWx0OnBhdGhfYWNjZXNzIiwicGF0aCI6InRyYW5zZm9ybS9lbmNvZGUvdXNlci1tY3AtdHJhbnNmb3JtIiwiY2FwYWJpbGl0aWVzIjpbImNyZWF0ZSIsInVwZGF0ZSJdfSx7InR5cGUiOiJ2YXVsdDpwYXRoX2FjY2VzcyIsInBhdGgiOiJzeXMvbGVhc2VzL3Jldm9rZSIsImNhcGFiaWxpdGllcyI6WyJ1cGRhdGUiXX1dLCJncm91cHMiOlsiYWRtaW4iXSwicHJlZmVycmVkX3VzZXJuYW1lIjoiYWRtaW4ifQ.WloZ_wpCeqg6cpNs0KclmH4tck0Prz0kYd2ezUdHKE6kkntF4nEoKaokvwuOsgPKrY3-SAzBd0XLq2LfN7dQeHEQdyRP1opVirsAO0WeuL10ntRSITFZWqtF_OEYjK2km_xeeY8bbhkjsGZgk6rRm4fJ-26zf2QkZP-SPK8WvQam_9FtuFKh-8aukcH-KxI3bacRjxl0bngjhkIT26Bcd5qhFXQ8YmQBxFyjzvhcfpRDZAewvHMT-BwcKsFaHu33GeN8WtZlDDCsOasxDhpD3IAQ--ISkWXq6XXOZ6_-sPPdk_KkNHw1HnOx81jGFd2K_VyMC9Z1FRpCoqrnVgSOqw'

curl -s -X POST http://localhost:18000/v1/agent/query \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "list all users"}
    ]
  }'

python scripts/demo_expired_jwt_rejected.py


CT-02.4: 
POD=$(kubectl --context local-minikube-demo get pod -l app=ai-agent -o jsonpath='{.items[0].metadata.name}')

kubectl --context local-minikube-demo debug "$POD" -c curl-$(date +%s) \
  --image=curlimages/curl --target=ai-agent -q --attach=true -- \
  curl -sS -m 15 -i -X POST http://user-mcp.virtual.consul:8090/mcp \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_all_users","arguments":{}}}'


CT-03.1:usuando o usuario "admin" crie um usuario

CT-03.2: python3 scripts/opa_gov_latency.py 2>&1 | (head -8; echo ...; tail -4)

CT-04.1: usando o usuario "user" tentar criar um usuario

CT-04.2: python3 scripts/demo_expired_jwt_rejected.py

CT-04.3: delete user bloqueado pela tool (delete users not in the catalog) show rego policy

CT-05.1: Identificação de LoA Insuficiente pelo PDP
CT-05.2: Execução com Sucesso do Fluxo de Step-up Auth
CT-05.3: Re-tentativa e Sucesso da Operação Após Step-up Auth
CT-05.4: Cancelamento do Step-up Auth pelo Usuário (Cenário Negativo)

CT-06.1: Validação de Logs de Auditoria e Não-Repúdio. 

CT-06.2: 
kubectl --context local-minikube-demo -n opa scale deploy/opa-server --replicas=0

kubectl --context local-minikube-demo logs deploy/litellm-gateway -c litellm-gateway --tail=200 | grep -i "opa_unreachable\|OPA PDP failed"

kubectl --context local-minikube-demo -n opa scale deploy/opa-server --replicas=1
kubectl --context local-minikube-demo -n opa rollout status deploy/opa-server
