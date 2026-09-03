"""
fussball.de answers a request carrying the wrong kind of ID with an empty body.
Without the checks covered here that is indistinguishable from "no games", so the
API used to reply 200 with an empty list - the caller saw nothing and no error.
"""

import pytest
from unittest.mock import patch

from fussball_api.cache import FetchedResponse
from fussball_api.crawler import (
    WrongIdTypeError,
    get_club_next_games,
    get_club_teams,
    get_team_next_games,
    get_team_table,
)

CLUB_ID = "00ES8GNBDG000077VV0AG08LVUPGND5I"
TEAM_ID = "011MIB6FFS000000VTVG0001VTR8C1K7"

# Enough markup for the probe to count the response as "this ID exists".
NON_EMPTY_HTML = '<div id="id-team-matchplan-table"><table><tbody></tbody></table></div>'


def _response(text: str, status_code: int = 200) -> FetchedResponse:
    return FetchedResponse(
        url="https://www.fussball.de/probe",
        status_code=status_code,
        headers={},
        content=text.encode("utf-8"),
        text=text,
    )


def _upstream(*, club_teams: str = "", team_games: str = "", team_table: str = ""):
    """
    Builds a fetch_url stub that answers per endpoint family, so a test can describe
    exactly which resource the ID really belongs to.
    """

    def _fetch(url: str, *args, **kwargs) -> FetchedResponse:
        if "ajax.club.teams" in url:
            return _response(club_teams)
        if "ajax.team.table" in url:
            return _response(team_table)
        if "ajax.team." in url:
            return _response(team_games)
        if "ajax.club." in url:
            return _response("")  # club game lists mirror an unknown club ID
        return _response("")

    return _fetch


@pytest.mark.asyncio
@patch("fussball_api.crawler.fetch_url")
async def test_team_id_on_club_games_endpoint_raises(mock_fetch):
    mock_fetch.side_effect = _upstream(team_games=NON_EMPTY_HTML)

    with pytest.raises(WrongIdTypeError) as excinfo:
        await get_club_next_games(TEAM_ID)

    assert excinfo.value.actual == "team"
    assert excinfo.value.expected == "club"
    assert TEAM_ID in str(excinfo.value)
    assert "/api/team/" in str(excinfo.value)


@pytest.mark.asyncio
@patch("fussball_api.crawler.fetch_url")
async def test_team_id_on_club_teams_endpoint_raises(mock_fetch):
    mock_fetch.side_effect = _upstream(team_games=NON_EMPTY_HTML)

    with pytest.raises(WrongIdTypeError):
        await get_club_teams(TEAM_ID)


@pytest.mark.asyncio
@patch("fussball_api.crawler.fetch_url")
async def test_club_id_on_team_games_endpoint_raises(mock_fetch):
    mock_fetch.side_effect = _upstream(club_teams=NON_EMPTY_HTML)

    with pytest.raises(WrongIdTypeError) as excinfo:
        await get_team_next_games(CLUB_ID)

    assert excinfo.value.actual == "club"
    assert excinfo.value.expected == "team"
    assert "/api/club/" in str(excinfo.value)


@pytest.mark.asyncio
@patch("fussball_api.crawler.fetch_url")
async def test_club_id_on_table_endpoint_raises(mock_fetch):
    mock_fetch.side_effect = _upstream(club_teams=NON_EMPTY_HTML)

    with pytest.raises(WrongIdTypeError):
        await get_team_table(CLUB_ID)


@pytest.mark.asyncio
@patch("fussball_api.crawler.fetch_url")
async def test_valid_team_without_table_still_returns_none(mock_fetch):
    """
    Regression guard: Ue50 and youth teams legitimately have no table, and
    fussball.de returns an empty body for them too. Those must not become a 404.
    """
    mock_fetch.side_effect = _upstream(team_games=NON_EMPTY_HTML, team_table="")

    assert await get_team_table(TEAM_ID) is None


@pytest.mark.asyncio
@patch("fussball_api.crawler.fetch_url")
async def test_unknown_id_stays_an_empty_list(mock_fetch):
    """An ID that belongs to neither resource is not misreported as the wrong type."""
    mock_fetch.side_effect = _upstream()

    assert await get_club_next_games("TOTALLY-UNKNOWN") == []


@pytest.mark.asyncio
@patch("fussball_api.crawler.fetch_url")
async def test_failed_request_does_not_raise(mock_fetch):
    """A network failure is not an ID problem and must keep the previous behaviour."""
    mock_fetch.return_value = None

    assert await get_club_next_games(CLUB_ID) == []
    assert await get_club_teams(CLUB_ID) == []
    assert await get_team_table(TEAM_ID) is None


@pytest.fixture
def client():
    from fussball_api.main import app
    from fussball_api.security import get_api_key

    app.dependency_overrides[get_api_key] = lambda: None
    from fastapi.testclient import TestClient

    yield TestClient(app)
    app.dependency_overrides.clear()


def test_endpoint_answers_404_instead_of_an_empty_list(client):
    """The whole point: the caller gets told what went wrong, not an empty array."""
    with patch(
        "fussball_api.main.get_club_next_games",
        side_effect=WrongIdTypeError(TEAM_ID, expected="club", actual="team"),
    ):
        resp = client.get(f"/api/club/{TEAM_ID}/next_games")

    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert TEAM_ID in detail
    assert "/api/team/" in detail
