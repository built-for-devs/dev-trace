"""Unit tests for free_sources.validate() — no network required."""
import pytest
from unittest.mock import patch
from providers.free_sources import validate


class TestValidateSyntax:
    def test_valid_email(self):
        result = validate("someone@example.com")
        assert result["signals"]["syntax_valid"] is True
        assert result["domain"] == "example.com"

    def test_invalid_no_at(self):
        result = validate("notanemail")
        assert result["signals"]["syntax_valid"] is False
        assert result["domain"] is None
        assert result["confidence_contrib"] == 0.0

    def test_invalid_no_domain(self):
        result = validate("user@")
        assert result["signals"]["syntax_valid"] is False

    def test_invalid_spaces(self):
        result = validate("user @example.com")
        assert result["signals"]["syntax_valid"] is False


class TestValidateDisposable:
    def test_disposable_flagged(self):
        result = validate("user@mailinator.com")
        assert result["is_disposable"] is True
        assert result["signals"]["disposable"] is True

    def test_disposable_reduces_confidence(self):
        with patch("providers.free_sources.HAVE_DNS", False):
            result = validate("user@mailinator.com")
        # 0.3 (syntax) - 0.5 (disposable penalty) floored at 0; no mx bonus without DNS
        assert result["confidence_contrib"] == 0.0

    def test_warning_set_in_trace(self):
        result = validate("user@mailinator.com")
        assert result["is_disposable"] is True


class TestValidateFreemail:
    def test_gmail_is_freemail(self):
        result = validate("user@gmail.com")
        assert result["is_freemail"] is True
        assert result["signals"]["freemail"] is True

    def test_corporate_not_freemail(self):
        result = validate("user@builtfor.dev")
        assert result["is_freemail"] is False


class TestValidateConfidence:
    def test_confidence_syntax_only_no_dns(self):
        with patch("providers.free_sources.HAVE_DNS", False):
            result = validate("user@example.com")
        # 0.3 for syntax, mx_ok is None so no bonus
        assert result["confidence_contrib"] == 0.3

    def test_confidence_with_mx(self):
        with patch("providers.free_sources.HAVE_DNS", True), \
             patch("providers.free_sources.dns") as mock_dns:
            mock_dns.resolver.resolve.return_value = ["mx1.example.com"]
            result = validate("user@example.com")
        assert result["confidence_contrib"] == 0.6  # 0.3 syntax + 0.3 mx

    def test_confidence_mx_failure(self):
        with patch("providers.free_sources.HAVE_DNS", True), \
             patch("providers.free_sources.dns") as mock_dns:
            mock_dns.resolver.resolve.side_effect = Exception("NXDOMAIN")
            result = validate("user@baddomain.xyz")
        assert result["signals"]["mx_valid"] is False
        assert result["confidence_contrib"] == 0.3  # syntax only

    def test_no_socket_fallback(self):
        """Verify the blocking socket.gethostbyname fallback is not called."""
        with patch("providers.free_sources.HAVE_DNS", False), \
             patch("socket.gethostbyname") as mock_socket:
            validate("user@example.com")
        mock_socket.assert_not_called()


class TestValidateMxUnknown:
    def test_mx_none_when_no_dnspython(self):
        with patch("providers.free_sources.HAVE_DNS", False):
            result = validate("user@example.com")
        assert result["signals"]["mx_valid"] is None
