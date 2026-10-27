"""OIDC authorization-code adapter with PKCE and strict ID-token validation."""

import base64
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from pydantic import BaseModel, field_validator

from ..backend.accounts import Accounts, Forbidden, Unauthorized, digest
from ..backend.models import BackendConfig, OIDCProvider
from ..backend.ports import Store
from ..config import secret
from ..ports import Clock


class Profile(BaseModel):
    """Optional OIDC presentation claims; never used to identify or authorize users."""

    name: str | None = None
    preferred_username: str | None = None

    @field_validator("name", "preferred_username", mode="before")
    @classmethod
    def readable_name(cls, value: object) -> str | None:
        if not isinstance(value, str):
            return None
        value = " ".join(value.split())
        return value if value.isprintable() and 0 < len(value) <= 256 else None

    @property
    def display_name(self) -> str | None:
        return self.name or self.preferred_username


class OIDC:
    def __init__(self, config: BackendConfig, store: Store, clock: Clock, accounts: Accounts):
        self.config, self.store, self.clock, self.accounts = config, store, clock, accounts

    @staticmethod
    def metadata(client: httpx.Client, provider: OIDCProvider) -> dict:
        response = client.get(provider.issuer.rstrip("/") + "/.well-known/openid-configuration")
        response.raise_for_status()
        metadata = response.json()
        if metadata.get("issuer") != provider.issuer:
            raise Unauthorized("OIDC discovery issuer mismatch")
        for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not str(metadata.get(key, "")).startswith("https://"):
                raise Unauthorized("OIDC endpoints must use HTTPS")
        return metadata

    def start(self, name: str, invitation: str | None = None) -> tuple[str, str]:
        auth = self.config.authentication
        if not auth.oidc.login and not auth.oidc.registration:
            raise Forbidden("OIDC is disabled")
        provider = auth.providers[name]
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        binding = secrets.token_urlsafe(32)
        callback = self.config.publicUrl.rstrip("/") + "/api/v1/auth/oidc/" + name + "/callback"
        with httpx.Client(timeout=10, trust_env=False) as client:
            metadata = self.metadata(client, provider)
        with self.store.transaction():
            self.store.put(
                "oidc-state",
                digest(state),
                {
                    "provider": name,
                    "nonce": nonce,
                    "verifier": verifier,
                    "binding": digest(binding),
                    "expires": (self.clock.now() + timedelta(minutes=5)).isoformat(),
                    "invitation": invitation,
                    "callback": callback,
                },
            )
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        url = (
            metadata["authorization_endpoint"]
            + "?"
            + urlencode(
                {
                    "client_id": provider.clientId,
                    "response_type": "code",
                    "scope": "openid profile",
                    "redirect_uri": callback,
                    "state": state,
                    "nonce": nonce,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                }
            )
        )
        return url, binding

    def finish(self, name: str, state: str, code: str, binding: str) -> dict:
        with self.store.transaction():
            saved = self.store.get("oidc-state", digest(state))
            if (
                not saved
                or saved["provider"] != name
                or saved["binding"] != digest(binding)
                or saved["expires"] <= self.clock.now().isoformat()
            ):
                raise Unauthorized("Invalid or expired OIDC state")
            self.store.delete("oidc-state", digest(state))
        provider = self.config.authentication.providers[name]
        try:
            with httpx.Client(timeout=10, trust_env=False) as client:
                metadata = self.metadata(client, provider)
                response = client.post(
                    metadata["token_endpoint"],
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": saved["callback"],
                        "code_verifier": saved["verifier"],
                    },
                    auth=httpx.BasicAuth(provider.clientId, secret(provider.clientSecretFile)),
                )
                response.raise_for_status()
                tokens = response.json()
                token = tokens["id_token"]
                keys_response = client.get(metadata["jwks_uri"])
                keys_response.raise_for_status()
                header = jwt.get_unverified_header(token)
                if header.get("alg") not in {"RS256", "ES256"}:
                    raise Unauthorized("Unsupported ID-token signing algorithm")
                keys = [
                    key
                    for key in keys_response.json()["keys"]
                    if key.get("kid") == header.get("kid") and key.get("use", "sig") == "sig"
                ]
                if len(keys) != 1:
                    raise Unauthorized("OIDC signing key is missing or ambiguous")
                claims = jwt.decode(
                    token,
                    jwt.PyJWK.from_dict(keys[0]).key,
                    algorithms=[header["alg"]],
                    audience=provider.clientId,
                    issuer=provider.issuer,
                    options={"require": ["iss", "sub", "aud", "exp", "iat", "nonce"]},
                )
                if (
                    claims["nonce"] != saved["nonce"]
                    or claims.get("azp", provider.clientId) != provider.clientId
                ):
                    raise Unauthorized("OIDC nonce or authorized party mismatch")
                if (
                    isinstance(claims["aud"], list)
                    and len(claims["aud"]) > 1
                    and "azp" not in claims
                ):
                    raise Unauthorized("OIDC token requires an authorized party")
                profile = Profile.model_validate(claims)
                endpoint = metadata.get("userinfo_endpoint")
                access_token = tokens.get("access_token")
                if not profile.display_name and endpoint and access_token:
                    if not isinstance(endpoint, str) or not isinstance(access_token, str):
                        raise Unauthorized("Invalid OIDC UserInfo endpoint or access token")
                    url = urlsplit(endpoint)
                    if url.scheme != "https" or not url.hostname or url.username or url.password:
                        raise Unauthorized("OIDC UserInfo requires HTTPS without credentials")
                    response = client.get(
                        endpoint, headers={"Authorization": "Bearer " + access_token}
                    )
                    response.raise_for_status()
                    info = response.json()
                    if not isinstance(info, dict) or info.get("sub") != claims["sub"]:
                        raise Unauthorized("OIDC UserInfo subject mismatch")
                    profile = Profile.model_validate(info)
        except (httpx.HTTPError, jwt.PyJWTError, KeyError, ValueError) as exc:
            raise Unauthorized("OIDC exchange or token validation failed") from exc
        return self.accounts.oidc_account(
            provider.issuer,
            claims["sub"],
            saved["invitation"],
            display_name=profile.display_name,
        )
