"""The app must only ever talk to local endpoints (privacy guarantee).

Every outbound request host is decided in settings: the Ollama base URL is
the sole inference/telemetry destination the backend can reach, the server
bind address decides who can call the app, and CORS origins decide which
pages may read its responses. All three must be loopback so no prompt can
leave this machine by configuration alone.
"""

from urllib.parse import urlparse

from backend.app.config import Settings, load_settings

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _assert_loopback_url(url: str) -> None:
    parsed = urlparse(url)
    assert parsed.scheme in ("http", "https"), f"unexpected scheme in {url!r}"
    assert parsed.hostname in LOOPBACK_HOSTS, f"non-loopback host in {url!r}"


def test_default_settings_are_loopback_only() -> None:
    settings = Settings()
    _assert_loopback_url(settings.ollama_base_url)
    assert settings.host in LOOPBACK_HOSTS
    for origin in settings.cors_origins:
        _assert_loopback_url(origin)


def test_effective_settings_from_env_are_loopback_only() -> None:
    """Same guarantee for the settings the running app actually loads
    (defaults plus any `.env` overrides)."""
    settings = load_settings()
    _assert_loopback_url(settings.ollama_base_url)
    assert settings.host in LOOPBACK_HOSTS
    for origin in settings.cors_origins:
        _assert_loopback_url(origin)
