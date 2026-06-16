"""Unit tests for providers/github_source.py — HTTP calls are fully mocked."""
from __future__ import annotations
import pytest
from unittest.mock import patch, MagicMock
from providers.github_source import (
    _derive_oss,
    _derive_seniority,
    _derive_role,
    _search_by_email,
    enrich,
    MIN_RESOLVE_CONFIDENCE,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------
FAKE_USER = {
    "login": "tessak22",
    "name": "Tessa Kriesel",
    "html_url": "https://github.com/tessak22",
    "bio": "DevRel person",
    "location": "Minneapolis",
    "company": "@mozilla",
    "blog": "https://tessakriesel.com",
    "avatar_url": "https://avatars.githubusercontent.com/u/1?v=4",
    "twitter_username": "tessak22",
    "created_at": "2015-01-01T00:00:00Z",
}


def _enrich_with_mocks(gravatar_login, email, user=FAKE_USER,
                       search=(None, 0.0), langs=([], 0, []), contribs=None):
    """Helper: run enrich() with all network calls mocked."""
    with patch("providers.github_source._user", return_value=(user, None)), \
         patch("providers.github_source._search_by_email", return_value=search), \
         patch("providers.github_source._repo_languages", return_value=langs), \
         patch("providers.github_source._contribution_activity", return_value=contribs):
        return enrich(gravatar_login, email)


# ---------------------------------------------------------------------------
# _derive_oss
# ---------------------------------------------------------------------------
class TestDeriveOss:
    def test_zero_contributions(self):
        assert _derive_oss(5, 0) == ("None", 0.6)

    def test_minimal_contributions(self):
        label, _ = _derive_oss(5, 50)
        assert label == "Minimal"

    def test_moderate_contributions(self):
        label, _ = _derive_oss(5, 200)
        assert label == "Moderate"

    def test_active_contributions(self):
        assert _derive_oss(5, 600) == ("Active", 0.75)

    def test_no_repos_fallback(self):
        assert _derive_oss(0, None) == ("None", 0.5)

    def test_minimal_repos_fallback(self):
        label, _ = _derive_oss(3, None)
        assert label == "Minimal"

    def test_moderate_repos_fallback(self):
        label, _ = _derive_oss(10, None)
        assert label == "Moderate"

    def test_active_repos_fallback(self):
        label, _ = _derive_oss(25, None)
        assert label == "Active"

    def test_contributions_take_priority_over_repos(self):
        # 0 contributions but many repos → "None" from contribution branch
        label, conf = _derive_oss(100, 0)
        assert label == "None"
        assert conf == 0.6


# ---------------------------------------------------------------------------
# _derive_seniority
# ---------------------------------------------------------------------------
class TestDeriveSeniority:
    def test_none_input(self):
        label, conf = _derive_seniority(None)
        assert label is None
        assert conf == 0.0

    def test_invalid_date_string(self):
        label, conf = _derive_seniority("not-a-date")
        assert label is None
        assert conf == 0.0

    def test_early_career(self):
        label, conf = _derive_seniority("2024-06-01T00:00:00Z")
        assert label == "Early Career"
        assert conf == 0.25

    def test_mid_career(self):
        label, conf = _derive_seniority("2019-01-01T00:00:00Z")
        assert label == "Mid-Career"
        assert conf == 0.25

    def test_established(self):
        label, conf = _derive_seniority("2013-01-01T00:00:00Z")
        assert label == "Established"
        assert conf == 0.20

    def test_all_confidence_low(self):
        for date in ("2024-01-01T00:00:00Z", "2019-01-01T00:00:00Z", "2010-01-01T00:00:00Z"):
            _, conf = _derive_seniority(date)
            assert conf <= 0.25


# ---------------------------------------------------------------------------
# _derive_role
# ---------------------------------------------------------------------------
class TestDeriveRole:
    def test_frontend_from_langs(self):
        label, _ = _derive_role(["react", "css"], [])
        assert label == "Frontend"

    def test_fullstack_when_frontend_and_backend_signals(self):
        label, _ = _derive_role(["react", "python"], [])
        assert label == "Fullstack"

    def test_devops_from_langs(self):
        label, _ = _derive_role(["terraform", "docker"], [])
        assert label == "DevOps"

    def test_data_from_langs(self):
        label, _ = _derive_role(["pytorch", "jupyter"], [])
        assert label == "Data"

    def test_backend_from_langs(self):
        label, _ = _derive_role(["go", "python"], [])
        assert label == "Backend"

    def test_no_signals(self):
        label, conf = _derive_role([], [])
        assert label is None
        assert conf == 0.0

    def test_topics_contribute(self):
        label, _ = _derive_role([], ["kubernetes", "devops"])
        assert label == "DevOps"

    def test_langs_and_topics_combined(self):
        label, _ = _derive_role(["react"], ["backend", "api"])
        assert label == "Fullstack"


# ---------------------------------------------------------------------------
# _search_by_email
# ---------------------------------------------------------------------------
class TestSearchByEmail:
    def _mock_get(self, items, status=200):
        m = MagicMock()
        m.status_code = status
        m.json.return_value = {"items": items}
        return m

    @patch("providers.github_source.requests.get")
    def test_single_match_returns_08(self, mock_get):
        mock_get.return_value = self._mock_get([{"login": "tessak22"}])
        login, conf = _search_by_email("me@tessak22.com")
        assert login == "tessak22"
        assert conf == 0.8

    @patch("providers.github_source.requests.get")
    def test_multiple_matches_returns_06(self, mock_get):
        mock_get.return_value = self._mock_get([{"login": "a"}, {"login": "b"}])
        login, conf = _search_by_email("shared@example.com")
        assert login == "a"
        assert conf == 0.6

    @patch("providers.github_source.requests.get")
    def test_no_results(self, mock_get):
        mock_get.return_value = self._mock_get([])
        login, conf = _search_by_email("nobody@nowhere.com")
        assert login is None
        assert conf == 0.0

    @patch("providers.github_source.requests.get")
    def test_403_rate_limited(self, mock_get):
        mock_get.return_value = self._mock_get([], status=403)
        login, conf = _search_by_email("user@example.com")
        assert login is None
        assert conf == 0.0

    @patch("providers.github_source.requests.get")
    def test_422_unprocessable(self, mock_get):
        mock_get.return_value = self._mock_get([], status=422)
        login, conf = _search_by_email("bad query")
        assert login is None

    @patch("providers.github_source.requests.get")
    def test_network_error(self, mock_get):
        mock_get.side_effect = Exception("timeout")
        login, conf = _search_by_email("user@example.com")
        assert login is None
        assert conf == 0.0

    @patch("providers.github_source.requests.get")
    def test_query_includes_email(self, mock_get):
        mock_get.return_value = self._mock_get([])
        _search_by_email("me@tessak22.com")
        call_params = mock_get.call_args[1]["params"]
        assert "me@tessak22.com" in call_params["q"]
        assert "in:email" in call_params["q"]


# ---------------------------------------------------------------------------
# enrich()
# ---------------------------------------------------------------------------
class TestEnrich:
    def test_gravatar_login_resolves(self):
        result = _enrich_with_mocks("tessak22", "me@tessak22.com")
        assert result["resolved"] is True
        assert result["match_confidence"] == 0.85
        assert result["login"] == "tessak22"

    def test_email_search_resolves_without_gravatar(self):
        result = _enrich_with_mocks(None, "me@tessak22.com", search=("tessak22", 0.8))
        assert result["resolved"] is True
        assert result["match_confidence"] == 0.8

    def test_gravatar_takes_priority_over_email_search(self):
        result = _enrich_with_mocks("tessak22", "me@tessak22.com", search=("other_user", 0.8))
        assert result["match_confidence"] == 0.85
        assert result["login"] == "tessak22"

    def test_low_confidence_local_part_not_resolved(self):
        """email local part "me" → conf 0.35, below MIN_RESOLVE_CONFIDENCE."""
        with patch("providers.github_source._user", return_value=(FAKE_USER, None)), \
             patch("providers.github_source._search_by_email", return_value=(None, 0.0)):
            result = enrich(None, "me@tessak22.com")
        assert result["resolved"] is False
        assert any(c["origin"] == "email_derived" for c in result["candidates"])

    def test_min_resolve_confidence_threshold(self):
        assert MIN_RESOLVE_CONFIDENCE == 0.5

    def test_rate_limited(self):
        with patch("providers.github_source._user", return_value=(None, "rate_limited")), \
             patch("providers.github_source._search_by_email", return_value=(None, 0.0)):
            result = enrich("tessak22", "me@tessak22.com")
        assert result["rate_limited"] is True
        assert result["resolved"] is False

    def test_no_match_anywhere(self):
        with patch("providers.github_source._user", return_value=(None, None)), \
             patch("providers.github_source._search_by_email", return_value=(None, 0.0)):
            result = enrich(None, "nobody@nowhere.com")
        assert result["resolved"] is False

    def test_github_url_in_fields(self):
        result = _enrich_with_mocks("tessak22", "me@tessak22.com")
        assert "github_url" in result["fields"]
        assert result["fields"]["github_url"]["value"] == "https://github.com/tessak22"

    def test_company_lowercase_title_cased(self):
        result = _enrich_with_mocks("tessak22", "me@tessak22.com",
                                    user={**FAKE_USER, "company": "@mozilla"})
        assert result["fields"]["current_company"]["value"] == "Mozilla"

    def test_company_mixed_case_preserved(self):
        result = _enrich_with_mocks("tessak22", "me@tessak22.com",
                                    user={**FAKE_USER, "company": "GitHub"})
        assert result["fields"]["current_company"]["value"] == "GitHub"

    def test_company_all_caps_preserved(self):
        result = _enrich_with_mocks("tessak22", "me@tessak22.com",
                                    user={**FAKE_USER, "company": "IBM"})
        assert result["fields"]["current_company"]["value"] == "IBM"

    def test_company_multi_word_lowercase_title_cased(self):
        result = _enrich_with_mocks("tessak22", "me@tessak22.com",
                                    user={**FAKE_USER, "company": "built for devs"})
        assert result["fields"]["current_company"]["value"] == "Built For Devs"

    def test_dedup_same_login_from_gravatar_and_search(self):
        """Same login from Gravatar and email search should not duplicate candidates."""
        result = _enrich_with_mocks("tessak22", "me@tessak22.com",
                                    search=("tessak22", 0.8))
        assert result["resolved"] is True
        assert result["match_confidence"] == 0.85  # Gravatar wins

    def test_languages_in_fields_when_returned(self):
        result = _enrich_with_mocks("tessak22", "me@tessak22.com",
                                    langs=(["TypeScript", "Python"], 5, []))
        assert result["fields"]["languages"]["value"] == ["TypeScript", "Python"]

    def test_contributions_in_fields_when_returned(self):
        result = _enrich_with_mocks("tessak22", "me@tessak22.com", contribs=450)
        assert result["fields"]["annual_contributions"]["value"] == 450

    def test_seniority_derived(self):
        result = _enrich_with_mocks("tessak22", "me@tessak22.com")
        assert "seniority" in result["fields"]
        assert result["fields"]["seniority"]["derived"] is True
        assert result["fields"]["seniority"]["confidence"] <= 0.25
