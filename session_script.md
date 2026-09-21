# CT-01.1: Autenticação de Usuário com Fator Único (LoA=1)
 login no web app
# CT-01.2: Autenticação Multifator / MFA (LoA=2)
 crie um usuario com o usuario admin
# CT-01.3: Autenticação Forte / Biometria (LoA=3)
 skipped
# CT-01.4: Falha na Autenticação por Credenciais Inválidas (Cenário Negativo)
 login with wrong user
# CT-01.5: Validação da Integridade e Assinatura do JWT no PEP
## 1 Port-forward the ai-agent Service (same as the demo script does)
```
kubectl --context local-minikube-demo port-forward deploy/ai-agent 18000:8000 &
```
```
python scripts/demo_expired_jwt_rejected.py
```
## 2. or do it by hand
```
JWT=<FORGED_JWT>

curl -s -X POST http://localhost:18000/v1/agent/query \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "Create a new user: first name Test, last name Curl, email test.curl@example.test"}
    ]
  }'
```

# CT-02.1: Handshake mTLS Válido com Extração de Common Name (CN) 
kubectl --context local-minikube-demo logs deploy/litellm-gateway -c litellm-gateway | grep admit_mesh

# CT-02.2: Rejeição de mTLS com Certificado Sem CN Autorizado (Cenário Negativo)

## 1. Atualizar Consul para forcar o deny entre web e litellm-gateway
```
kubectl --context local-minikube-demo patch serviceintentions litellm-gateway \
  --type=json \
  -p '[{"op":"replace","path":"/spec/sources/1/action","value":"deny"}]'
```
## 2. Verificar se as ServiceIntentions foram Atualizadas
```
kubectl --context local-minikube-demo get serviceintentions litellm-gateway
```
## 3. No Browser tentar listar os usuarios (com qualquer usuario)

### Do Browser Things

## 4. reverter pra nao quebrar o resto da demo.
```
kubectl --context local-minikube-demo patch serviceintentions litellm-gateway \
  --type=json \
  -p '[{"op":"replace","path":"/spec/sources/1/action","value":"allow"}]'
```
# CT-02.3: Rejeição de Certificado Expirado ou Revogado (Cenário Negativo)
python scripts/demo_expired_jwt_rejected.py


# CT-02.4: Bloqueio de Tentativa de Bypass do PEP (Cenário Negativo)
POD=$(kubectl --context local-minikube-demo get pod -l app=ai-agent -o jsonpath='{.items[0].metadata.name}')

kubectl --context local-minikube-demo debug "$POD" -c curl-$(date +%s) \
  --image=curlimages/curl --target=ai-agent -q --attach=true -- \
  curl -sS -m 15 -i -X POST http://user-mcp.virtual.consul:8090/mcp \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_all_users","arguments":{}}}'


# CT-03.1: Fluxo Ponta a Ponta Concedido com LoA=2 e CN Autorizado

### No Browser use o usuario admin para criar um novo usuario

# CT-03.2: Validação de Decisão Cacheada/Performática no PDP
```
python3 scripts/opa_gov_latency.py 2>&1 | (head -8; echo ...; tail -4)
```

# CT-04.1: Negação por Falta de Perfil de Negócio no PDP
### No Browser use o usuario user para criar um novo usuario

# CT-04.2: Negação por Expiração do Token JWT Durante o Fluxo
```
python3 scripts/demo_expired_jwt_rejected.py
```

# CT-04.3: : Negação por Ação Proibida entre Agentes de IA

### delete user bloqueado pela tool (delete users not in the catalog) show rego policy

# CT-05.1: Identificação de LoA Insuficiente pelo PDP

# CT-05.2: Execução com Sucesso do Fluxo de Step-up Auth

# CT-05.3: Re-tentativa e Sucesso da Operação Após Step-up Auth

# CT-05.4: Cancelamento do Step-up Auth pelo Usuário (Cenário Negativo)

# CT-06.1: Validação de Logs de Auditoria e Não-Repúdio. 

CT-06.2: 
kubectl --context local-minikube-demo -n opa scale deploy/opa-server --replicas=0

kubectl --context local-minikube-demo logs deploy/litellm-gateway -c litellm-gateway --tail=200 | grep -i "opa_unreachable\|OPA PDP failed"

kubectl --context local-minikube-demo -n opa scale deploy/opa-server --replicas=1
kubectl --context local-minikube-demo -n opa rollout status deploy/opa-server
