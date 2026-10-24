"""Bring your own key for Azure OpenAI: stored, encrypted, applied, never returned."""
from __future__ import annotations

from cryptography.fernet import Fernet

from api.app.services import settings as svc

AZ = {"X-User": "architect.lee"}


def _with_app_secret(app) -> None:
    """A real Fernet key, so encrypt and decrypt use the same one across requests."""
    app.state.settings.app_secret = Fernet.generate_key().decode()


async def test_byok_stores_the_key_encrypted_and_applies_it(client, app):
    _with_app_secret(app)
    body = {"azure": {"endpoint": "https://my-aoai.openai.azure.com",
                      "api_key": "super-secret-key",
                      "deployments": {"strong": "gpt-5", "standard": "gpt-5-mini", "efficient": "gpt-5-nano"}}}
    r = await client.put("/settings", json=body, headers=AZ)
    assert r.status_code == 200, r.text
    view = r.json()
    az = view["azure_openai"]
    assert az["endpoint"] == "https://my-aoai.openai.azure.com"
    assert az["key_set"] is True and az["source"] == "stored"
    assert az["deployments"]["strong"] == "gpt-5"
    # the key itself never leaves the server
    assert "super-secret-key" not in r.text

    async with app.state.sessionmaker() as s:
        stored = await svc.load_stored(s)
    blob = stored[svc.AZURE_KEY]
    assert blob["api_key_enc"] and "super-secret-key" not in blob["api_key_enc"]

    # and it reaches the live settings, which is what every provider path reads
    assert app.state.settings.azure_openai_api_key == "super-secret-key"
    assert app.state.settings.azure_openai_deployment_efficient == "gpt-5-nano"


async def test_byok_endpoint_must_be_a_url(client):
    r = await client.put("/settings", json={"azure": {"endpoint": "my-aoai.openai.azure.com"}}, headers=AZ)
    assert r.status_code == 422


async def test_byok_keeps_the_key_when_only_the_endpoint_is_edited(client, app):
    _with_app_secret(app)
    await client.put("/settings", json={"azure": {"endpoint": "https://a.openai.azure.com",
                                                  "api_key": "k1"}}, headers=AZ)
    r = await client.put("/settings", json={"azure": {"endpoint": "https://b.openai.azure.com"}}, headers=AZ)
    assert r.json()["azure_openai"]["key_set"] is True
    assert app.state.settings.azure_openai_api_key == "k1"


async def test_byok_can_be_cleared(client, app):
    _with_app_secret(app)
    await client.put("/settings", json={"azure": {"endpoint": "https://a.openai.azure.com",
                                                  "api_key": "k1"}}, headers=AZ)
    r = await client.put("/settings", json={"azure": {"clear": True}}, headers=AZ)
    assert r.json()["azure_openai"]["source"] == "not configured"
    async with app.state.sessionmaker() as s:
        stored = await svc.load_stored(s)
    assert stored[svc.AZURE_KEY]["api_key_enc"] == ""


async def test_a_key_is_refused_without_app_secret(client, app):
    """Storing a key that could never be decrypted again would look like success and fail later."""
    app.state.settings.app_secret = ""
    r = await client.put("/settings", json={"azure": {"api_key": "k1"}}, headers=AZ)
    assert r.status_code == 422
    assert "APP_SECRET" in r.json()["detail"]
    # the endpoint alone is not a secret, so it is still accepted
    r = await client.put("/settings", json={"azure": {"endpoint": "https://a.openai.azure.com"}}, headers=AZ)
    assert r.status_code == 200
