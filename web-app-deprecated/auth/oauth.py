"""IBM Verify OAuth 2.0 Authorization Code Flow."""

import json
import secrets
import urllib.parse

import jwt
import requests
from jwt.algorithms import RSAAlgorithm

from config import (
    IBM_VERIFY_CLIENT_ID,
    IBM_VERIFY_CLIENT_SECRET,
    IBM_VERIFY_REDIRECT_URI,
    IBM_VERIFY_SCOPES,
    IBM_VERIFY_TENANT_URL,
)
from observability import build_outbound_headers, get_logger

# ── Config from environment ──────────────────────────────────────────────────
CLIENT_ID = IBM_VERIFY_CLIENT_ID
CLIENT_SECRET = IBM_VERIFY_CLIENT_SECRET
TENANT_URL = IBM_VERIFY_TENANT_URL
REDIRECT_URI = IBM_VERIFY_REDIRECT_URI
SCOPES = IBM_VERIFY_SCOPES
LOGGER = get_logger("auth.oauth")

# ── Endpoint URLs ─────────────────────────────────────────────────────────────
_BASE = f"{TENANT_URL}/oidc/endpoint/default"
AUTHORIZE_URL = f"{_BASE}/authorize"
TOKEN_URL     = f"{_BASE}/token"
JWKS_URL      = f"{_BASE}/jwks"
USERINFO_URL  = f"{_BASE}/userinfo"
LOGOUT_URL    = f"{_BASE}/logout"


def generate_state() -> str:
    """Return a CSRF-safe random state token."""
    return secrets.token_urlsafe(16)


def get_authorization_url(state: str) -> str:
    """Build the IBM Verify /authorize redirect URL."""
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
        "prompt": "login",   # force IBM Verify to always show login form
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def exchange_code_for_tokens(code: str) -> dict:
    """
    Exchange an authorization code for tokens.

    Returns dict with keys: access_token, id_token, token_type, expires_in, scope.
    Raises requests.HTTPError on failure.
    """
    LOGGER.debug("Exchanging authorization code for tokens")
    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        headers=build_outbound_headers({"Accept": "application/json"}),
        timeout=15,
    )
    response.raise_for_status()
    LOGGER.debug("Token exchange completed with status %s", response.status_code)
    return response.json()


def get_jwks() -> dict:
    """Fetch IBM Verify JWKS (JSON Web Key Set)."""
    LOGGER.debug("Fetching JWKS from IBM Verify")
    response = requests.get(JWKS_URL, headers=build_outbound_headers(), timeout=10)
    response.raise_for_status()
    return response.json()


def validate_id_token(id_token: str) -> dict:
    """
    Validate the id_token JWT signature using IBM Verify JWKS.

    Returns the decoded claims dict.
    Raises jwt.PyJWTError on invalid token.
    """
    unverified_header = jwt.get_unverified_header(id_token)
    key_id = unverified_header.get("kid")
    jwks = get_jwks()
    matching_key = next((key for key in jwks.get("keys", []) if key.get("kid") == key_id), None)
    if matching_key is None:
        raise jwt.PyJWTError("Unable to find a matching JWKS signing key.")

    LOGGER.debug("Validating id_token signature using JWKS key %s", key_id)
    signing_key = RSAAlgorithm.from_jwk(json.dumps(matching_key))
    claims = jwt.decode(
        id_token,
        signing_key,
        algorithms=["RS256"],
        audience=CLIENT_ID,
        options={"verify_exp": True},
    )
    return claims


def extract_user_info(claims: dict) -> dict:
    """Extract display-safe user info from id_token claims."""
    name = claims.get("name") or claims.get("preferred_username") or claims.get("sub", "User")
    email = claims.get("email", "")
    initials = "".join(part[0].upper() for part in name.split()[:2]) if name else "?"
    return {"name": name, "email": email, "initials": initials, "sub": claims.get("sub", "")}


def get_logout_url(id_token: str = "") -> str:
    """
    Build the IBM Verify RP-initiated logout URL (OIDC end-session endpoint).

    After logout IBM Verify redirects back to REDIRECT_URI (the app login page).
    Passing id_token_hint ensures the correct SSO session is terminated.
    """
    params: dict = {"post_logout_redirect_uri": REDIRECT_URI}
    if id_token:
        params["id_token_hint"] = id_token
    return f"{LOGOUT_URL}?{urllib.parse.urlencode(params)}"
