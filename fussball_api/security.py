import logging
import re
import secrets
from typing import Dict

from fastapi import Header, HTTPException, status

from .config import settings

logger = logging.getLogger(__name__)

# Ships in .env.example. Once real keys are configured it must not grant access,
# otherwise anyone who has seen the repository could call a misconfigured instance.
PLACEHOLDER_API_KEY = "your-secret-api-key"


def _unquote(value: str) -> str:
    """
    Strips whitespace and one matching pair of surrounding quotes.

    Values read from a .env file are unquoted by pydantic-settings, but a real process
    environment variable - how Coolify, Docker and Kubernetes inject configuration -
    keeps the quotes literally. Without this, API_KEYS="a:b" would register the key
    as 'b"' and reject the caller.

    :param value: The raw configuration value.
    :return: The value without surrounding whitespace or quotes.
    """
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    return value


def _valid_api_keys() -> Dict[str, str]:
    """
    Builds the mapping of accepted API keys to their label.

    Keys come from ``API_KEYS`` (comma-separated, each entry either ``<label>:<key>``
    or a bare ``<key>``) and from the legacy single ``API_KEY`` setting. Labels only
    serve logging, so a single consumer can be revoked by removing its entry without
    invalidating everyone else's key.

    :return: A dict mapping each accepted key to a human readable label.
    """
    keys: Dict[str, str] = {}

    # A deployment UI offers a textarea, so entries may be separated by newlines or
    # semicolons rather than commas. None of these can occur inside a generated key.
    for raw_entry in re.split(r"[,;\r\n]+", _unquote(settings.API_KEYS)):
        entry = raw_entry.strip()
        if not entry:
            continue

        label, separator, key = entry.partition(":")
        if separator:
            label, key = label.strip(), _unquote(key)
        else:
            label, key = "unnamed", _unquote(entry)

        if key:
            keys[key] = label or "unnamed"

    legacy_key = _unquote(settings.API_KEY)
    if legacy_key and not (keys and legacy_key == PLACEHOLDER_API_KEY):
        keys.setdefault(legacy_key, "legacy")

    return keys


async def get_api_key(api_key: str = Header(..., alias="X-API-Key")):
    """
    Dependency function to verify the API key from the request header.

    :param api_key: The API key passed in the 'X-API-Key' header.
    :return: The label of the matching key, for logging purposes.
    :raises HTTPException: If the API key is invalid.
    """
    valid_keys = _valid_api_keys()
    if not valid_keys:
        logger.error(
            "No API key is configured. Set API_KEYS (or API_KEY); rejecting all requests."
        )

    # compare_digest only accepts ASCII strings, so compare the encoded bytes.
    provided = api_key.encode("utf-8")
    for key, label in valid_keys.items():
        if secrets.compare_digest(provided, key.encode("utf-8")):
            logger.debug("Request authenticated with API key '%s'.", label)
            return label

    logger.warning("Rejected request with an invalid API key.")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API Key",
    )


def describe_configured_keys() -> str:
    """
    Summarises the configured keys for the startup log.

    Only labels and counts are reported - never the keys themselves.

    :return: A human readable summary.
    """
    keys = _valid_api_keys()
    if not keys:
        return "no key configured, every request will be rejected"

    labels = sorted(keys.values())
    return f"{len(keys)} key(s) accepted, labels: {', '.join(labels)}"
