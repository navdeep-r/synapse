import hmac
import hashlib
import json
import pytest
from httpx import ASGITransport, AsyncClient
from app.main import create_app
from app.config import Settings
import tempfile
from pathlib import Path

@pytest.fixture
def app_client_settings():
    settings = Settings(
        data_dir=Path(tempfile.mkdtemp(prefix="synapse-webhook-")),
        strict_prompts=True,
        llm_provider="stub",
        github_webhook_secret="testsecret"
    )
    return create_app(settings)

@pytest.mark.asyncio
async def test_github_webhook_invalid_signature(app_client_settings):
    app = app_client_settings
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            response = await client.post(
                "/api/v1/github/webhook",
                json={"test": "data"},
                headers={
                    "x-github-event": "push",
                    "x-github-delivery": "12345",
                    "x-hub-signature-256": "sha256=invalid"
                }
            )
            assert response.status_code == 401
            assert response.json() == {"detail": "Invalid signature"}

@pytest.mark.asyncio
async def test_github_webhook_missing_signature(app_client_settings):
    app = app_client_settings
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            response = await client.post(
                "/api/v1/github/webhook",
                json={"test": "data"},
                headers={
                    "x-github-event": "push",
                    "x-github-delivery": "12345"
                }
            )
            assert response.status_code == 401
            assert response.json() == {"detail": "Missing signature"}
