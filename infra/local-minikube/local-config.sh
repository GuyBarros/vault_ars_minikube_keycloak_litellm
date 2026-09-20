#!/bin/bash

# Local-only settings for the minikube workflow.
# When set, bootstrap.sh skips minikube driver auto-detection and uses this.
MINIKUBE_DRIVER="podman"
CONSUL_IMAGE_VERSION="2.0.1-ent"
VAULT_IMAGE_VERSION="2.1.0-ent"
POSTGRES_ADMIN_PASSWORD="HashiCorp123!"
ID_ORG="ibm"
ID_BU="hr"
ID_DEPT="payroll"
ID_SVC_GROUP="employee-profile"
