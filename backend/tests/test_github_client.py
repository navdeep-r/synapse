"""Tests for GitHub Connector client."""

import pytest
from unittest.mock import AsyncMock, patch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from github.client import GitHubAppClient


def generate_mock_private_key() -> str:
    """Generate a valid RSA private key for testing JWT signing."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    return pem.decode("utf-8")


@pytest.fixture
def github_client():
    private_key = generate_mock_private_key()
    return GitHubAppClient(app_id="12345", private_key=private_key)


def test_generate_jwt(github_client):
    jwt_token = github_client._generate_jwt()
    assert jwt_token is not None
    assert isinstance(jwt_token, str)


@pytest.mark.asyncio
async def test_get_installation_token(github_client):
    from unittest.mock import MagicMock
    mock_response = MagicMock()
    import datetime
    future = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1)
    mock_response.json.return_value = {
        "token": "ghs_mocked_token_123",
        "expires_at": future.isoformat().replace("+00:00", "Z"),
    }
    mock_response.raise_for_status = MagicMock()

    # We patch httpx.AsyncClient.post to return our mock response
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        token = await github_client.get_installation_token("1111")
        assert token == "ghs_mocked_token_123"
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert "https://api.github.com/app/installations/1111/access_tokens" in args[0]

        # Call again to ensure caching works
        token2 = await github_client.get_installation_token("1111")
        assert token2 == "ghs_mocked_token_123"
        # Should not have called post again
        mock_post.assert_called_once()
