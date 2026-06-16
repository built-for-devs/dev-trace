"""Unit tests for providers/envelope.py — pure functions, no network."""
import pytest
from providers.envelope import field, merge_field


# --------------------------------------------------------------------------
# field()
# --------------------------------------------------------------------------
class TestField:
    def test_basic(self):
        f = field("Alice", 0.9, [{"provider": "github"}])
        assert f["value"] == "Alice"
        assert f["confidence"] == 0.9
        assert f["public"] is True
        assert f["derived"] is False
        assert f["conflicts"] == []
        assert f["sources"] == [{"provider": "github"}]

    def test_confidence_rounded(self):
        f = field("x", 0.123456, [])
        assert f["confidence"] == 0.12

    def test_derived_flag(self):
        f = field("Backend", 0.4, [], derived=True)
        assert f["derived"] is True

    def test_conflicts_default_empty_list(self):
        f = field("v", 0.5, [])
        assert f["conflicts"] == []

    def test_conflicts_passed_through(self):
        existing = [{"value": "old", "sources": [], "confidence": 0.3}]
        f = field("new", 0.7, [], conflicts=existing)
        assert f["conflicts"] == existing


# --------------------------------------------------------------------------
# merge_field()
# --------------------------------------------------------------------------
class TestMergeField:
    def _src(self, name: str) -> list[dict]:
        return [{"provider": name}]

    def test_new_key_added(self):
        profile: dict = {}
        merge_field(profile, "name", field("Alice", 0.8, self._src("gh")))
        assert profile["name"]["value"] == "Alice"

    def test_skips_none_value(self):
        profile: dict = {}
        merge_field(profile, "name", field(None, 0.8, self._src("gh")))
        assert "name" not in profile

    def test_skips_empty_string(self):
        profile: dict = {}
        merge_field(profile, "name", field("", 0.8, self._src("gh")))
        assert "name" not in profile

    def test_skips_empty_list(self):
        profile: dict = {}
        merge_field(profile, "langs", field([], 0.8, self._src("gh")))
        assert "langs" not in profile

    def test_skips_none_envelope(self):
        profile: dict = {}
        merge_field(profile, "name", None)
        assert "name" not in profile

    def test_agreement_bumps_confidence(self):
        profile: dict = {}
        merge_field(profile, "name", field("Alice", 0.7, self._src("gravatar")))
        merge_field(profile, "name", field("Alice", 0.7, self._src("github")))
        assert profile["name"]["confidence"] == 0.75

    def test_agreement_unions_sources(self):
        profile: dict = {}
        merge_field(profile, "name", field("Alice", 0.7, self._src("gravatar")))
        merge_field(profile, "name", field("Alice", 0.7, self._src("github")))
        providers = [s["provider"] for s in profile["name"]["sources"]]
        assert "gravatar" in providers
        assert "github" in providers

    def test_agreement_no_duplicate_sources(self):
        profile: dict = {}
        src = self._src("github")
        merge_field(profile, "name", field("Alice", 0.7, src))
        merge_field(profile, "name", field("Alice", 0.7, src))
        assert len(profile["name"]["sources"]) == 1

    def test_agreement_confidence_caps_at_0_99(self):
        profile: dict = {}
        merge_field(profile, "name", field("Alice", 0.99, self._src("a")))
        merge_field(profile, "name", field("Alice", 0.99, self._src("b")))
        assert profile["name"]["confidence"] <= 0.99

    def test_conflict_higher_new_wins(self):
        profile: dict = {}
        merge_field(profile, "loc", field("NYC", 0.5, self._src("gravatar")))
        merge_field(profile, "loc", field("SF", 0.8, self._src("github")))
        assert profile["loc"]["value"] == "SF"
        assert profile["loc"]["confidence"] == 0.8
        assert len(profile["loc"]["conflicts"]) == 1
        assert profile["loc"]["conflicts"][0]["value"] == "NYC"

    def test_conflict_lower_new_loses(self):
        profile: dict = {}
        merge_field(profile, "loc", field("NYC", 0.8, self._src("github")))
        merge_field(profile, "loc", field("SF", 0.5, self._src("gravatar")))
        assert profile["loc"]["value"] == "NYC"
        assert len(profile["loc"]["conflicts"]) == 1
        assert profile["loc"]["conflicts"][0]["value"] == "SF"

    def test_immutable_original_not_mutated(self):
        """merge_field must reassign profile[key], not mutate the original dict."""
        original = field("Alice", 0.7, self._src("gravatar"))
        profile: dict = {"name": original}
        merge_field(profile, "name", field("Alice", 0.7, self._src("github")))
        # The original envelope object must be untouched
        assert original["sources"] == self._src("gravatar")
        assert original["confidence"] == 0.7

    def test_conflict_does_not_mutate_existing_conflicts_list(self):
        """Conflict merging should not append to the existing field's conflicts in place."""
        original = field("NYC", 0.5, self._src("gravatar"))
        profile: dict = {"loc": original}
        merge_field(profile, "loc", field("SF", 0.8, self._src("github")))
        assert original["conflicts"] == []  # original unchanged

    # -- list union ---------------------------------------------------------

    def test_list_of_dicts_unioned_by_url(self):
        """Social links from two sources should be unioned, not conflicted."""
        gravatar_links = [
            {"service": "twitter", "url": "https://x.com/tessak22"},
            {"service": "linkedin", "url": "https://linkedin.com/in/tessak22"},
        ]
        github_links = [
            {"service": "twitter", "url": "https://x.com/tessak22"},
        ]
        profile: dict = {}
        merge_field(profile, "social_links", field(gravatar_links, 0.7, self._src("gravatar")))
        merge_field(profile, "social_links", field(github_links, 0.85, self._src("github")))
        urls = [i["url"] for i in profile["social_links"]["value"]]
        assert "https://x.com/tessak22" in urls
        assert "https://linkedin.com/in/tessak22" in urls

    def test_list_union_no_duplicates(self):
        links = [{"service": "twitter", "url": "https://x.com/tessak22"}]
        profile: dict = {}
        merge_field(profile, "social_links", field(links, 0.7, self._src("gravatar")))
        merge_field(profile, "social_links", field(links, 0.85, self._src("github")))
        assert len(profile["social_links"]["value"]) == 1

    def test_list_union_sources_merged(self):
        links_a = [{"service": "twitter", "url": "https://x.com/a"}]
        links_b = [{"service": "linkedin", "url": "https://linkedin.com/a"}]
        profile: dict = {}
        merge_field(profile, "social_links", field(links_a, 0.7, self._src("gravatar")))
        merge_field(profile, "social_links", field(links_b, 0.85, self._src("github")))
        providers = [s["provider"] for s in profile["social_links"]["sources"]]
        assert "gravatar" in providers
        assert "github" in providers

    def test_list_union_no_conflict_recorded(self):
        links_a = [{"service": "twitter", "url": "https://x.com/a"}]
        links_b = [{"service": "linkedin", "url": "https://linkedin.com/a"}]
        profile: dict = {}
        merge_field(profile, "social_links", field(links_a, 0.7, self._src("gravatar")))
        merge_field(profile, "social_links", field(links_b, 0.85, self._src("github")))
        assert profile["social_links"]["conflicts"] == []

    def test_list_of_strings_unioned(self):
        profile: dict = {}
        merge_field(profile, "tags", field(["python", "go"], 0.7, self._src("a")))
        merge_field(profile, "tags", field(["go", "rust"], 0.8, self._src("b")))
        assert set(profile["tags"]["value"]) == {"python", "go", "rust"}
