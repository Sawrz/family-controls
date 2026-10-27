"""Exercise our OIDC binding and account policy using a simulated signed provider."""

import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from family_controls.backend.models import OIDCProvider
from test_backend import central as central


@pytest.fixture
def provider(central, monkeypatch, tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    public.update(kid="test-key", use="sig")
    secret = tmp_path / "oidc-secret"
    secret.write_text("test-oidc-secret")
    central.config.authentication.providers["test"] = OIDCProvider(
        issuer="https://issuer.test", clientId="family", clientSecretFile=str(secret)
    )
    central.config.authentication.oidc.registration = True
    settings = {"nonce": "", "overrides": {}, "metadata": {}, "userinfo": {}}

    def transport(request):
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": "https://issuer.test",
                    "authorization_endpoint": "https://issuer.test/authorize",
                    "token_endpoint": "https://issuer.test/token",
                    "jwks_uri": "https://issuer.test/keys",
                    **settings["metadata"],
                },
            )
        if request.url.path == "/keys":
            return httpx.Response(200, json={"keys": [public]})
        if request.url.path == "/userinfo":
            assert request.headers["Authorization"] == "Bearer test-access-token"
            return httpx.Response(200, json=settings["userinfo"])
        assert request.url.path == "/token"
        assert "code_verifier" in parse_qs(request.content.decode())
        claims = {
            "iss": "https://issuer.test",
            "sub": "parent-subject",
            "aud": "family",
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
            "nonce": settings["nonce"],
            **settings["overrides"],
        }
        return httpx.Response(
            200,
            json={
                "id_token": jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"}),
                "access_token": "test-access-token",
            },
        )

    original = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(transport), **kwargs),
    )

    def start():
        response = central.client.get("/api/v1/auth/oidc/test/start", follow_redirects=False)
        assert response.status_code == 302
        query = parse_qs(urlsplit(response.headers["location"]).query)
        assert query["code_challenge_method"] == ["S256"]
        settings["nonce"] = query["nonce"][0]
        return query["state"][0]

    return settings, start


def test_oidc_registration_approval_login_and_replay(central, provider):
    settings, start = provider
    settings["overrides"] = {"name": "  Alex   Müller ", "preferred_username": "alex"}
    state = start()
    result = central.client.get(f"/api/v1/auth/oidc/test/callback?state={state}&code=test")
    assert result.status_code == 200 and result.json()["status"] == "pending"
    username = result.json()["username"]
    assert result.json()["displayName"] == "Alex Müller"
    assert (
        central.client.get(f"/api/v1/auth/oidc/test/callback?state={state}&code=test").status_code
        == 401
    )
    central.client.post(f"/api/v1/accounts/{username}/approve", json={"role": "parent"})
    central.config.authentication.oidc.registration = False
    settings["overrides"] = {"name": "Alex Schmidt"}
    state = start()
    result = central.client.get(f"/api/v1/auth/oidc/test/callback?state={state}&code=test")
    assert result.status_code == 200 and "token" in result.json()
    token = result.json()["token"]
    me = central.client.get("/api/v1/me", headers={"Authorization": "Bearer " + token}).json()
    assert me["username"] == username and me["displayName"] == "Alex Schmidt"
    assert me["role"] == "parent" and me["status"] == "approved"
    assert len(central.store.items("accounts")) == 2


@pytest.mark.parametrize(
    "claims",
    [
        {"nonce": "wrong"},
        {"iss": "https://other.test"},
        {"aud": "another-client"},
        {"exp": 1},
        {"azp": "another-client"},
    ],
)
def test_invalid_signed_claims_cannot_create_an_account(central, provider, claims):
    settings, start = provider
    settings["overrides"] = claims
    state = start()
    assert (
        central.client.get(f"/api/v1/auth/oidc/test/callback?state={state}&code=test").status_code
        == 401
    )
    assert len(central.store.items("accounts")) == 1


def test_oidc_browser_binding_and_independent_login_switch(central, provider):
    _, start = provider
    central.config.authentication.oidc.login = False
    central.config.authentication.requireApproval = False
    state = start()
    binding = central.client.cookies.get("family_oidc")
    central.client.cookies.clear()
    assert (
        central.client.get(f"/api/v1/auth/oidc/test/callback?state={state}&code=test").status_code
        == 401
    )
    central.client.cookies.set("family_oidc", binding)
    result = central.client.get(f"/api/v1/auth/oidc/test/callback?state={state}&code=test")
    assert result.json()["status"] == "approved" and "token" not in result.json()
    central.config.authentication.oidc.registration = False
    assert central.client.get("/api/v1/auth/oidc/test/start").status_code == 403


def test_explicit_oidc_link_uses_existing_account_with_registration_disabled(central, provider):
    settings, start = provider
    settings["overrides"] = {"name": "Household owner"}
    central.config.authentication.oidc.registration = False
    assert (
        central.client.post(
            "/api/v1/identities/oidc",
            json={"issuer": "https://issuer.test", "subject": "parent-subject", "account": "admin"},
        ).status_code
        == 200
    )
    state = start()
    result = central.client.get(f"/api/v1/auth/oidc/test/callback?state={state}&code=test")
    assert "token" in result.json()
    assert central.accounts.authenticate(result.json()["token"])["username"] == "admin"
    assert central.accounts.authenticate(result.json()["token"])["displayName"] == "Household owner"
    assert len(central.store.items("accounts")) == 1


@pytest.mark.parametrize(
    ("claims", "expected"),
    [
        ({"preferred_username": "alex"}, "alex"),
        ({"name": ["invalid"], "preferred_username": "alex"}, "alex"),
        ({"name": "\u202eevil", "preferred_username": "alex"}, "alex"),
        ({"name": "x" * 257, "preferred_username": "alex"}, "alex"),
        ({"name": "  ", "preferred_username": None}, None),
    ],
)
def test_optional_profile_claims_fall_back_without_breaking_login(
    central, provider, claims, expected
):
    settings, start = provider
    settings["overrides"] = claims
    state = start()
    result = central.client.get(f"/api/v1/auth/oidc/test/callback?state={state}&code=test")
    assert result.status_code == 200
    account = result.json()
    assert account["displayName"] == (expected or account["username"])


def test_missing_profile_preserves_name_and_duplicate_names_do_not_merge_accounts(
    central, provider
):
    settings, start = provider
    settings["overrides"] = {"name": "Same name"}
    state = start()
    first = central.client.get(f"/api/v1/auth/oidc/test/callback?state={state}&code=test").json()
    settings["overrides"] = {}
    state = start()
    repeated = central.client.get(f"/api/v1/auth/oidc/test/callback?state={state}&code=test").json()
    assert repeated == first
    settings["overrides"] = {"sub": "another-parent", "name": "Same name"}
    state = start()
    second = central.client.get(f"/api/v1/auth/oidc/test/callback?state={state}&code=test").json()
    assert second["username"] != first["username"]
    assert second["displayName"] == first["displayName"]
    assert second["status"] == "pending"


@pytest.mark.parametrize("subject", ["parent-subject", "different-person", None])
def test_userinfo_names_require_the_verified_token_subject(central, provider, subject):
    settings, start = provider
    settings["metadata"] = {"userinfo_endpoint": "https://issuer.test/userinfo"}
    settings["userinfo"] = {"sub": subject, "name": "Alex Müller"}
    state = start()
    result = central.client.get(f"/api/v1/auth/oidc/test/callback?state={state}&code=test")
    if subject == "parent-subject":
        assert result.status_code == 200
        assert result.json()["displayName"] == "Alex Müller"
    else:
        assert result.status_code == 401
        assert len(central.store.items("accounts")) == 1


def test_userinfo_never_sends_tokens_over_http(central, provider):
    settings, start = provider
    settings["metadata"] = {"userinfo_endpoint": "http://issuer.test/userinfo"}
    state = start()
    result = central.client.get(f"/api/v1/auth/oidc/test/callback?state={state}&code=test")
    assert result.status_code == 401
    assert len(central.store.items("accounts")) == 1


def test_oidc_only_bootstrap_needs_no_password_file(tmp_path, clock):
    from family_controls.adapters.backend_sqlite import BackendStore
    from family_controls.adapters.passwords import ArgonPasswords
    from family_controls.backend.accounts import Accounts
    from family_controls.backend.models import Authentication

    config = Authentication.model_validate(
        {
            "password": {"login": False},
            "bootstrapOIDC": {"issuer": "https://issuer.test", "subject": "owner"},
            "providers": {
                "test": {
                    "issuer": "https://issuer.test",
                    "clientId": "family",
                    "clientSecretFile": "/unused",
                }
            },
        }
    )
    store = BackendStore(str(tmp_path / "accounts.sqlite"))
    try:
        accounts = Accounts(config, store, ArgonPasswords(), clock)
        owner = accounts.oidc_account("https://issuer.test", "owner", None)
        assert owner["username"] == "admin" and owner["password"] is None
        assert accounts.authenticate(accounts.session(owner, "oidc"))["role"] == "admin"
    finally:
        store.close()
