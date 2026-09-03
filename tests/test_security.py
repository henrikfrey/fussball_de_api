import asyncio

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from fussball_api import security
from fussball_api.config import settings


@pytest.fixture(autouse=True)
def clean_settings(monkeypatch):
    """Starts every test from the shipped defaults, so tests never leak into each other."""
    monkeypatch.setattr(settings, "API_KEY", security.PLACEHOLDER_API_KEY)
    monkeypatch.setattr(settings, "API_KEYS", "")


@pytest.fixture
def client():
    app = FastAPI()

    @app.get("/protected", dependencies=[Depends(security.get_api_key)])
    async def protected():
        return {"ok": True}

    return TestClient(app)


def _get(client, key=None):
    headers = {"X-API-Key": key} if key is not None else {}
    return client.get("/protected", headers=headers)


def test_missing_header_is_rejected(client):
    assert _get(client).status_code == 422


def test_legacy_single_api_key_still_works(client, monkeypatch):
    monkeypatch.setattr(settings, "API_KEY", "legacy-secret")

    assert _get(client, "legacy-secret").status_code == 200
    assert _get(client, "wrong").status_code == 401


def test_labelled_keys_are_accepted(client, monkeypatch):
    monkeypatch.setattr(settings, "API_KEYS", "henrik:key-henrik,cousin:key-cousin")

    assert _get(client, "key-henrik").status_code == 200
    assert _get(client, "key-cousin").status_code == 200
    assert _get(client, "key-unknown").status_code == 401


def test_bare_keys_without_label_are_accepted(client, monkeypatch):
    monkeypatch.setattr(settings, "API_KEYS", "key-a,key-b")

    assert _get(client, "key-a").status_code == 200
    assert _get(client, "key-b").status_code == 200


def test_whitespace_and_empty_entries_are_tolerated(client, monkeypatch):
    monkeypatch.setattr(settings, "API_KEYS", "  henrik : key-henrik ,, cousin:key-cousin  ,")

    assert _get(client, "key-henrik").status_code == 200
    assert _get(client, "key-cousin").status_code == 200


def test_revoking_one_key_leaves_the_others_valid(client, monkeypatch):
    monkeypatch.setattr(settings, "API_KEYS", "henrik:key-henrik,cousin:key-cousin")
    assert _get(client, "key-cousin").status_code == 200

    # Operator removes the cousin's entry from API_KEYS and redeploys.
    monkeypatch.setattr(settings, "API_KEYS", "henrik:key-henrik")

    assert _get(client, "key-cousin").status_code == 401
    assert _get(client, "key-henrik").status_code == 200


def test_placeholder_key_is_dropped_once_real_keys_exist(client, monkeypatch):
    """The .env.example default must never grant access on a configured instance."""
    monkeypatch.setattr(settings, "API_KEYS", "cousin:key-cousin")

    assert _get(client, security.PLACEHOLDER_API_KEY).status_code == 401
    assert _get(client, "key-cousin").status_code == 200


def test_legacy_key_and_api_keys_coexist(client, monkeypatch):
    monkeypatch.setattr(settings, "API_KEY", "legacy-secret")
    monkeypatch.setattr(settings, "API_KEYS", "cousin:key-cousin")

    assert _get(client, "legacy-secret").status_code == 200
    assert _get(client, "key-cousin").status_code == 200


def test_no_configured_key_rejects_everything(client, monkeypatch):
    monkeypatch.setattr(settings, "API_KEY", "")
    monkeypatch.setattr(settings, "API_KEYS", "")

    assert _get(client, "anything").status_code == 401
    assert _get(client, "").status_code == 401


def test_non_ascii_key_is_rejected_not_crashed(monkeypatch):
    """
    secrets.compare_digest raises TypeError on non-ASCII str, so get_api_key encodes
    first. HTTP headers cannot carry such a value, hence the dependency is called
    directly rather than through a TestClient.
    """
    monkeypatch.setattr(settings, "API_KEYS", "cousin:key-cousin")

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(security.get_api_key("schlüssel-mit-umlaut"))

    assert excinfo.value.status_code == 401


def test_label_is_returned_for_logging(monkeypatch):
    monkeypatch.setattr(settings, "API_KEYS", "cousin:key-cousin")

    app = FastAPI()

    @app.get("/whoami")
    async def whoami(label: str = Depends(security.get_api_key)):
        return {"label": label}

    client = TestClient(app)
    resp = client.get("/whoami", headers={"X-API-Key": "key-cousin"})

    assert resp.status_code == 200
    assert resp.json() == {"label": "cousin"}


def test_quoted_api_keys_value_is_accepted(client, monkeypatch):
    """
    Coolify and similar UIs inject real process env vars, where pydantic-settings
    keeps surrounding quotes literally (unlike values read from a .env file).
    """
    monkeypatch.setattr(settings, "API_KEYS", '"henrik:key-henrik,cousin:key-cousin"')

    assert _get(client, "key-henrik").status_code == 200
    assert _get(client, "key-cousin").status_code == 200
    assert _get(client, '"key-cousin"').status_code == 401


def test_single_quoted_api_keys_value_is_accepted(client, monkeypatch):
    monkeypatch.setattr(settings, "API_KEYS", "'cousin:key-cousin'")

    assert _get(client, "key-cousin").status_code == 200


def test_quoted_single_key_is_accepted(client, monkeypatch):
    monkeypatch.setattr(settings, "API_KEY", '"legacy-secret"')

    assert _get(client, "legacy-secret").status_code == 200


def test_quotes_around_an_individual_entry_are_stripped(client, monkeypatch):
    monkeypatch.setattr(settings, "API_KEYS", 'cousin:"key-cousin",henrik:key-henrik')

    assert _get(client, "key-cousin").status_code == 200
    assert _get(client, "key-henrik").status_code == 200


def test_quoted_placeholder_is_still_dropped(client, monkeypatch):
    """Quoting must not smuggle the .env.example placeholder back in."""
    monkeypatch.setattr(settings, "API_KEY", f'"{security.PLACEHOLDER_API_KEY}"')
    monkeypatch.setattr(settings, "API_KEYS", "cousin:key-cousin")

    assert _get(client, security.PLACEHOLDER_API_KEY).status_code == 401
    assert _get(client, "key-cousin").status_code == 200


def test_newline_separated_entries_are_accepted(client, monkeypatch):
    """
    A textarea in a deployment UI invites one key per line. Splitting on commas only
    would swallow every entry but the first into one unusable key.
    """
    monkeypatch.setattr(settings, "API_KEYS", "website:key-website\nrico:key-rico")

    assert _get(client, "key-website").status_code == 200
    assert _get(client, "key-rico").status_code == 200


def test_crlf_separated_entries_are_accepted(client, monkeypatch):
    monkeypatch.setattr(settings, "API_KEYS", "website:key-website\r\nrico:key-rico")

    assert _get(client, "key-website").status_code == 200
    assert _get(client, "key-rico").status_code == 200


def test_semicolon_separated_entries_are_accepted(client, monkeypatch):
    monkeypatch.setattr(settings, "API_KEYS", "website:key-website;rico:key-rico")

    assert _get(client, "key-website").status_code == 200
    assert _get(client, "key-rico").status_code == 200


def test_mixed_separators_are_accepted(client, monkeypatch):
    monkeypatch.setattr(
        settings, "API_KEYS", "website:key-website,\n rico:key-rico ;; henrik:key-henrik"
    )

    for key in ("key-website", "key-rico", "key-henrik"):
        assert _get(client, key).status_code == 200, key
