from __future__ import annotations

# Self-managed Level of Assurance. Not IdP-issued. LOA_BASELINE is every
# authenticated OBO caller; LOA_ELEVATED is a caller whose JWT carries a
# Keycloak step-up (acr) for this specific action (see
# storage/postgres_repo.py:_jwt_for_vault). There is no LOA=3 here — only one
# elevation mechanism exists, so only one elevated level is honest to claim.
LOA_BASELINE = 1
LOA_ELEVATED = 2

ALLOW = "ALLOW"
