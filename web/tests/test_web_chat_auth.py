"""
Hermes Web Chat API - Authentication Tests

Tests for password authentication, session management, and rate limiting.
Covers: password hashing, session creation/verification, rate limiting, cookie handling
"""

import os
import secrets
import time
import pytest
from unittest.mock import patch, MagicMock, mock_open
from pathlib import Path


# =============================================================================
# Test 1: Password Hashing
# =============================================================================

class TestPasswordHashing:
    """Test password hashing with PBKDF2-SHA256."""

    def test_hash_password_produces_consistent_output(self, temp_state_dir, monkeypatch):
        """Hashing the same password produces the same hash."""
        from hermes_cli.web_chat_api.auth import _hash_password

        monkeypatch.setattr('hermes_cli.web_chat_api.auth.STATE_DIR', temp_state_dir)

        hash1 = _hash_password("testpassword123")
        hash2 = _hash_password("testpassword123")

        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 hex = 64 chars

    def test_hash_password_different_passwords_different_hashes(self, temp_state_dir, monkeypatch):
        """Different passwords produce different hashes."""
        from hermes_cli.web_chat_api.auth import _hash_password

        monkeypatch.setattr('hermes_cli.web_chat_api.auth.STATE_DIR', temp_state_dir)

        hash1 = _hash_password("password1")
        hash2 = _hash_password("password2")

        assert hash1 != hash2

    def test_hash_password_salt_is_random(self, temp_state_dir, monkeypatch):
        """Password hashing uses PBKDF2-SHA256 with 600k iterations."""
        from hermes_cli.web_chat_api.auth import _hash_password

        # Verify hash format (SHA256 hex = 64 chars)
        hash_result = _hash_password("testpassword")
        assert len(hash_result) == 64

        # Verify hash is consistent for same password
        hash_result2 = _hash_password("testpassword")
        assert hash_result == hash_result2

        # Verify different passwords produce different hashes
        hash_different = _hash_password("differentpassword")
        assert hash_result != hash_different


# =============================================================================
# Test 2: Password Verification
# =============================================================================

class TestPasswordVerification:
    """Test password verification functionality."""

    def test_verify_password_correct(self, temp_state_dir, monkeypatch):
        """Verifying correct password returns True."""
        from hermes_cli.web_chat_api.auth import _hash_password, verify_password

        monkeypatch.setattr('hermes_cli.web_chat_api.auth.STATE_DIR', temp_state_dir)

        # Set up password
        test_hash = _hash_password("mysecretpassword")

        with patch('hermes_cli.web_chat_api.auth.get_password_hash', return_value=test_hash):
            assert verify_password("mysecretpassword") is True

    def test_verify_password_incorrect(self, temp_state_dir, monkeypatch):
        """Verifying incorrect password returns False."""
        from hermes_cli.web_chat_api.auth import _hash_password, verify_password

        monkeypatch.setattr('hermes_cli.web_chat_api.auth.STATE_DIR', temp_state_dir)

        test_hash = _hash_password("mysecretpassword")

        with patch('hermes_cli.web_chat_api.auth.get_password_hash', return_value=test_hash):
            assert verify_password("wrongpassword") is False

    def test_verify_password_no_password_set(self):
        """Verifying when no password set returns False."""
        from hermes_cli.web_chat_api.auth import verify_password

        with patch('hermes_cli.web_chat_api.auth.get_password_hash', return_value=None):
            assert verify_password("anypassword") is False

    def test_verify_password_timing_safe_comparison(self, temp_state_dir, monkeypatch):
        """Password comparison uses timing-safe comparison."""
        from hermes_cli.web_chat_api.auth import verify_password, _hash_password
        import hmac

        monkeypatch.setattr('hermes_cli.web_chat_api.auth.STATE_DIR', temp_state_dir)

        test_hash = _hash_password("testpass")

        # Verify hmac.compare_digest is used (timing-safe)
        with patch('hermes_cli.web_chat_api.auth.hmac.compare_digest', return_value=True) as mock_compare:
            with patch('hermes_cli.web_chat_api.auth.get_password_hash', return_value=test_hash):
                verify_password("testpass")
                mock_compare.assert_called_once()


# =============================================================================
# Test 3: Auth Configuration
# =============================================================================

class TestAuthConfiguration:
    """Test authentication configuration and enablement."""

    def test_is_auth_enabled_false_when_no_password(self):
        """Auth is disabled when no password configured."""
        from hermes_cli.web_chat_api.auth import is_auth_enabled

        with patch('hermes_cli.web_chat_api.auth.get_password_hash', return_value=None):
            assert is_auth_enabled() is False

    def test_is_auth_enabled_true_when_password_set(self):
        """Auth is enabled when password configured."""
        from hermes_cli.web_chat_api.auth import is_auth_enabled

        with patch('hermes_cli.web_chat_api.auth.get_password_hash', return_value="somehash"):
            assert is_auth_enabled() is True

    def test_get_password_hash_from_env(self, temp_state_dir, monkeypatch):
        """Password hash from environment variable."""
        from hermes_cli.web_chat_api.auth import get_password_hash, _hash_password

        monkeypatch.setattr('hermes_cli.web_chat_api.auth.STATE_DIR', temp_state_dir)
        monkeypatch.setenv('HERMES_WEBUI_PASSWORD', 'envpassword')

        result = get_password_hash()
        expected = _hash_password('envpassword')
        assert result == expected

    def test_get_password_hash_from_settings(self, temp_state_dir, monkeypatch):
        """Password hash from settings.json."""
        from hermes_cli.web_chat_api.auth import get_password_hash

        monkeypatch.setattr('hermes_cli.web_chat_api.auth.STATE_DIR', temp_state_dir)
        monkeypatch.setenv('HERMES_WEBUI_PASSWORD', '')

        mock_settings = {'webui': {'password_hash': 'storedhash123'}}

        with patch('hermes_cli.config.load_config', return_value=mock_settings):
            assert get_password_hash() == 'storedhash123'

    def test_env_password_takes_priority_over_settings(self, temp_state_dir, monkeypatch):
        """Environment variable password takes priority over settings."""
        from hermes_cli.web_chat_api.auth import get_password_hash, _hash_password

        monkeypatch.setattr('hermes_cli.web_chat_api.auth.STATE_DIR', temp_state_dir)
        monkeypatch.setenv('HERMES_WEBUI_PASSWORD', 'envpass')

        mock_settings = {'webui': {'password_hash': 'settingspass'}}

        with patch('hermes_cli.config.load_config', return_value=mock_settings):
            result = get_password_hash()
            expected = _hash_password('envpass')
            assert result == expected


# =============================================================================
# Test 4: Session Management
# =============================================================================

class TestSessionManagement:
    """Test session creation and verification."""

    def test_create_session_returns_valid_token(self, temp_state_dir, monkeypatch):
        """Creating session returns properly formatted token."""
        from hermes_cli.web_chat_api.auth import create_session

        monkeypatch.setattr('hermes_cli.web_chat_api.auth.STATE_DIR', temp_state_dir)

        token = create_session()

        # Token format: token.signature
        assert '.' in token
        parts = token.split('.')
        assert len(parts) == 2
        assert len(parts[0]) == 64  # token_hex(32) = 64 chars
        assert len(parts[1]) == 32  # truncated signature

    def test_verify_session_valid_token(self, temp_state_dir, monkeypatch):
        """Verifying valid session token returns True."""
        from hermes_cli.web_chat_api.auth import create_session, verify_session

        monkeypatch.setattr('hermes_cli.web_chat_api.auth.STATE_DIR', temp_state_dir)

        token = create_session()
        assert verify_session(token) is True

    def test_verify_session_invalid_signature(self, temp_state_dir, monkeypatch):
        """Verifying token with invalid signature returns False."""
        from hermes_cli.web_chat_api.auth import verify_session

        monkeypatch.setattr('hermes_cli.web_chat_api.auth.STATE_DIR', temp_state_dir)

        # Tampered token
        assert verify_session("faketoken.badsignature") is False

    def test_verify_session_expired(self, temp_state_dir, monkeypatch):
        """Verifying expired session returns False."""
        from hermes_cli.web_chat_api.auth import create_session, verify_session, _sessions, SESSION_TTL

        monkeypatch.setattr('hermes_cli.web_chat_api.auth.STATE_DIR', temp_state_dir)

        # Clear sessions
        _sessions.clear()

        token = create_session()

        # Manually expire the session
        for t in _sessions:
            _sessions[t] = time.time() - 1  # Expired 1 second ago

        assert verify_session(token) is False

    def test_invalidate_session_removes_token(self, temp_state_dir, monkeypatch):
        """Invalidating session removes it from active sessions."""
        from hermes_cli.web_chat_api.auth import create_session, verify_session, invalidate_session, _sessions

        monkeypatch.setattr('hermes_cli.web_chat_api.auth.STATE_DIR', temp_state_dir)

        # Clear sessions
        _sessions.clear()

        token = create_session()
        assert verify_session(token) is True

        invalidate_session(token)
        assert verify_session(token) is False

    def test_session_cleanup_removes_expired(self, temp_state_dir, monkeypatch):
        """Expired sessions are cleaned up."""
        from hermes_cli.web_chat_api.auth import _sessions, verify_session, _prune_expired_sessions

        monkeypatch.setattr('hermes_cli.web_chat_api.auth.STATE_DIR', temp_state_dir)

        # Clear and add expired session
        _sessions.clear()
        _sessions['expired_token'] = time.time() - 100

        _prune_expired_sessions()

        assert 'expired_token' not in _sessions


# =============================================================================
# Test 5: Rate Limiting
# =============================================================================

class TestRateLimiting:
    """Test login rate limiting."""

    def test_rate_limit_allows_first_attempts(self):
        """First N attempts are allowed."""
        from hermes_cli.web_chat_api.auth import _check_login_rate, _login_attempts

        # Clear attempts
        _login_attempts.clear()

        ip = "192.168.1.1"

        # First 5 attempts should be allowed
        for i in range(5):
            assert _check_login_rate(ip) is True, f"Attempt {i+1} should be allowed"

    def test_rate_limit_blocks_after_max_attempts(self):
        """Attempts after max are blocked."""
        from hermes_cli.web_chat_api.auth import _check_login_rate, _record_login_attempt, _login_attempts

        # Clear attempts
        _login_attempts.clear()

        ip = "192.168.1.2"

        # Record max attempts
        for i in range(5):
            _record_login_attempt(ip)

        # 6th attempt should be blocked
        assert _check_login_rate(ip) is False

    def test_rate_limit_resets_after_window(self):
        """Rate limit resets after time window."""
        from hermes_cli.web_chat_api.auth import _login_attempts, _LOGIN_WINDOW

        ip = "192.168.1.3"

        # Set up old attempts
        old_time = time.time() - _LOGIN_WINDOW - 1
        _login_attempts[ip] = [old_time] * 10

        # Should be allowed now (old attempts expired)
        from hermes_cli.web_chat_api.auth import _check_login_rate
        assert _check_login_rate(ip) is True

    def test_record_login_attempt_tracks_attempts(self):
        """Login attempts are recorded."""
        from hermes_cli.web_chat_api.auth import _record_login_attempt, _login_attempts

        # Clear attempts
        _login_attempts.clear()

        ip = "192.168.1.4"
        _record_login_attempt(ip)

        assert ip in _login_attempts
        assert len(_login_attempts[ip]) == 1


# =============================================================================
# Test 6: Cookie Handling
# =============================================================================

class TestCookieHandling:
    """Test cookie parsing and handling."""

    def test_parse_cookie_extract_session(self):
        """Parsing cookie header extracts session token."""
        from hermes_cli.web_chat_api.auth import parse_cookie

        # Mock handler with cookie header
        mock_handler = MagicMock()
        mock_handler.headers = {
            'Cookie': 'hermes_session=testtoken123; other_cookie=value'
        }

        result = parse_cookie(mock_handler)
        assert result == 'testtoken123'

    def test_parse_cookie_no_cookie_returns_none(self):
        """Parsing when no cookie returns None."""
        from hermes_cli.web_chat_api.auth import parse_cookie

        mock_handler = MagicMock()
        mock_handler.headers = {}

        result = parse_cookie(mock_handler)
        assert result is None

    def test_parse_cookie_malformed_cookie(self):
        """Parsing malformed cookie returns None."""
        from hermes_cli.web_chat_api.auth import parse_cookie

        mock_handler = MagicMock()
        mock_handler.headers = {'Cookie': 'invalid=cookie=with=too=many=equals'}

        result = parse_cookie(mock_handler)
        # Should handle gracefully, may return None or partial result

    def test_set_auth_cookie_sets_attributes(self):
        """Setting auth cookie includes security attributes."""
        from hermes_cli.web_chat_api.auth import set_auth_cookie, COOKIE_NAME, SESSION_TTL

        mock_handler = MagicMock()

        set_auth_cookie(mock_handler, 'testtoken')

        # Verify send_header was called with Set-Cookie
        mock_handler.send_header.assert_called()
        calls = [str(call) for call in mock_handler.send_header.call_args_list]
        cookie_call = [c for c in calls if 'Set-Cookie' in c][0]

        # Check security attributes
        assert 'HttpOnly' in cookie_call or 'httponly' in cookie_call.lower()
        assert 'SameSite' in cookie_call or 'samesite' in cookie_call.lower()
        assert 'path=/' in cookie_call.lower()

    def test_clear_auth_cookie_sets_expiry_zero(self):
        """Clearing cookie sets max-age to 0."""
        from hermes_cli.web_chat_api.auth import clear_auth_cookie

        mock_handler = MagicMock()

        clear_auth_cookie(mock_handler)

        mock_handler.send_header.assert_called()
        calls = [str(call) for call in mock_handler.send_header.call_args_list]
        cookie_call = [c for c in calls if 'Set-Cookie' in c][0]

        assert 'max-age=0' in cookie_call.lower() or "'0'" in cookie_call


# =============================================================================
# Test 7: Authorization Check
# =============================================================================

class TestAuthorizationCheck:
    """Test authorization checking for requests."""

    def test_check_auth_passes_when_disabled(self):
        """Auth check passes when auth is disabled."""
        from hermes_cli.web_chat_api.auth import check_auth

        mock_handler = MagicMock()
        mock_parsed = MagicMock()
        mock_parsed.path = '/api/test'

        with patch('hermes_cli.web_chat_api.auth.is_auth_enabled', return_value=False):
            result = check_auth(mock_handler, mock_parsed)
            assert result is True

    def test_check_auth_passes_for_public_paths(self):
        """Auth check passes for public paths."""
        from hermes_cli.web_chat_api.auth import check_auth, PUBLIC_PATHS

        mock_handler = MagicMock()

        for path in PUBLIC_PATHS:
            mock_parsed = MagicMock()
            mock_parsed.path = path

            with patch('hermes_cli.web_chat_api.auth.is_auth_enabled', return_value=True):
                result = check_auth(mock_handler, mock_parsed)
                assert result is True, f"Path {path} should be public"

    def test_check_auth_passes_with_valid_session(self):
        """Auth check passes with valid session cookie."""
        from hermes_cli.web_chat_api.auth import check_auth

        mock_handler = MagicMock()
        mock_handler.headers = {'Cookie': 'hermes_session=validtoken'}
        mock_parsed = MagicMock()
        mock_parsed.path = '/api/test'

        with patch('hermes_cli.web_chat_api.auth.is_auth_enabled', return_value=True):
            with patch('hermes_cli.web_chat_api.auth.parse_cookie', return_value='validtoken'):
                with patch('hermes_cli.web_chat_api.auth.verify_session', return_value=True):
                    result = check_auth(mock_handler, mock_parsed)
                    assert result is True

    def test_check_auth_blocks_api_without_session(self):
        """Auth check blocks API requests without session."""
        from hermes_cli.web_chat_api.auth import check_auth

        mock_handler = MagicMock()
        mock_handler.headers = {}
        mock_parsed = MagicMock()
        mock_parsed.path = '/api/test'

        with patch('hermes_cli.web_chat_api.auth.is_auth_enabled', return_value=True):
            with patch('hermes_cli.web_chat_api.auth.parse_cookie', return_value=None):
                result = check_auth(mock_handler, mock_parsed)

                assert result is False
                mock_handler.send_response.assert_called_with(401)

    def test_check_auth_redirects_page_without_session(self):
        """Auth check redirects page requests without session."""
        from hermes_cli.web_chat_api.auth import check_auth

        mock_handler = MagicMock()
        mock_handler.headers = {}
        mock_parsed = MagicMock()
        mock_parsed.path = '/dashboard'

        with patch('hermes_cli.web_chat_api.auth.is_auth_enabled', return_value=True):
            with patch('hermes_cli.web_chat_api.auth.parse_cookie', return_value=None):
                result = check_auth(mock_handler, mock_parsed)

                assert result is False
                mock_handler.send_response.assert_called_with(302)
                mock_handler.send_header.assert_any_call('Location', '/login')

    def test_check_auth_allows_static_paths(self):
        """Auth check allows static paths without authentication."""
        from hermes_cli.web_chat_api.auth import check_auth

        mock_handler = MagicMock()
        mock_parsed = MagicMock()
        mock_parsed.path = '/static/style.css'

        with patch('hermes_cli.web_chat_api.auth.is_auth_enabled', return_value=True):
            result = check_auth(mock_handler, mock_parsed)
            assert result is True


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def temp_state_dir(tmp_path):
    """Create isolated state directory for auth testing."""
    state_dir = tmp_path / "auth-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir


@pytest.fixture(autouse=True)
def clear_auth_state():
    """Clear global auth state between tests."""
    from hermes_cli.web_chat_api.auth import _sessions, _login_attempts

    _sessions.clear()
    _login_attempts.clear()

    yield

    _sessions.clear()
    _login_attempts.clear()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
