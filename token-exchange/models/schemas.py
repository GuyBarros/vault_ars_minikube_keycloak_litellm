from pydantic import BaseModel, Field


class TokenRequest(BaseModel):
    vault_token: str = Field(..., min_length=1, description="Vault authentication token")
    role_name: str = Field(..., min_length=1, description="Vault OIDC role name")


class TokenResponse(BaseModel):
    identity_token: str = Field(..., description="Vault-signed OIDC Identity JWT")
    expires_at: int = Field(..., description="Token expiration as a Unix timestamp")
    cached: bool = Field(..., description="Whether the token was served from cache")


class OBOTokenRequest(BaseModel):
    subject_token: str = Field(
        ..., min_length=1, description="Caller's access token (JWT) to act on behalf of"
    )
    actor_token: str = Field(
        ..., min_length=1, description="Vault Identity JWT identifying the actor"
    )
    scope: str = Field(
        ...,
        min_length=1,
        description="Space-separated OAuth scopes (RFC 8693 'scope' parameter)",
    )


class SubjectStatusRequest(BaseModel):
    subject_token: str = Field(..., min_length=1, description="Caller's access token (JWT) to check")


class SubjectStatusResponse(BaseModel):
    active: bool = Field(
        ..., description="False once the token was revoked or its Keycloak session ended"
    )


class OBOTokenResponse(BaseModel):
    access_token: str = Field(
        ..., description="Keycloak access token issued on behalf of the subject"
    )
    cached: bool = Field(..., description="Whether the token was served from cache")
