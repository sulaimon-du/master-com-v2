"""Google sign-in: OpenID Connect with authorization code, PKCE, `state` and `nonce` (BRIEF §4.1).

The id_token signature is checked against Google's published keys, and audience, issuer,
expiry and nonce are checked too. Only verified email addresses are accepted.
"""

import base64
import hashlib
import hmac
import http.client
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import timedelta

import jwt
from jwt import PyJWKClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.security import hash_token, new_token
from app.core.time import utcnow
from app.models.auth import OAuthFlow

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"  # noqa: S105 (URL, not a secret)
JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("accounts.google.com", "https://accounts.google.com")
HTTP_TIMEOUT = 10
FLOW_TTL = timedelta(minutes=10)

# Module-level so tests can replace the key source.
jwk_client = PyJWKClient(JWKS_URI, cache_keys=True, timeout=HTTP_TIMEOUT)


class GoogleError(Exception):
    """Any failure in the Google exchange. The message is for logs only, never shown to users."""


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    name: str | None


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def new_code_verifier() -> str:
    return secrets.token_urlsafe(64)


def pkce_challenge(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode()).digest())


def begin_flow(
    db: Session, *, redirect_uri: str, client_id: str, user_id: int | None
) -> tuple[str, str]:
    """Store state, nonce and PKCE verifier for one flow. Returns (state, Google URL)."""
    state = new_token()
    nonce = new_token()
    verifier = new_code_verifier()
    db.add(
        OAuthFlow(
            state_hash=hash_token(state),
            nonce=nonce,
            code_verifier=verifier,
            user_id=user_id,
            expires_at=utcnow() + FLOW_TTL,
        )
    )
    db.flush()
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
    )
    return state, f"{AUTH_ENDPOINT}?{query}"


def take_flow(db: Session, state: str) -> OAuthFlow | None:
    """Return and delete the flow for `state`, so the callback can run only once."""
    return db.scalars(
        delete(OAuthFlow)
        .where(OAuthFlow.state_hash == hash_token(state), OAuthFlow.expires_at > utcnow())
        .returning(OAuthFlow)
    ).first()


def exchange_code(
    *, code: str, code_verifier: str, client_id: str, client_secret: str, redirect_uri: str
) -> str:
    """Trade the code for tokens at Google's token endpoint and return the id_token."""
    body = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        }
    ).encode()
    request = urllib.request.Request(
        TOKEN_ENDPOINT,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:  # noqa: S310
            payload = json.load(response)
    # URLError covers the request; OSError and HTTPException cover a connection dropped mid-read.
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as exc:
        raise GoogleError("token exchange failed") from exc
    if not isinstance(payload, dict):
        raise GoogleError("token response is not an object")
    token = payload.get("id_token")
    if not isinstance(token, str):
        raise GoogleError("no id_token in token response")
    return token


def verify_id_token(id_token: str, *, client_id: str, nonce: str) -> GoogleIdentity:
    try:
        signing_key = jwk_client.get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=client_id,
            issuer=ISSUERS,
            options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce", "email"]},
        )
    except (jwt.PyJWTError, jwt.PyJWKClientError) as exc:
        raise GoogleError("id_token rejected") from exc
    if not hmac.compare_digest(str(claims["nonce"]), nonce):
        raise GoogleError("nonce mismatch")
    if claims.get("email_verified") is not True:
        raise GoogleError("email not verified by Google")
    name = claims.get("name")
    return GoogleIdentity(
        sub=str(claims["sub"]),
        email=str(claims["email"]).lower(),
        name=name if isinstance(name, str) else None,
    )
