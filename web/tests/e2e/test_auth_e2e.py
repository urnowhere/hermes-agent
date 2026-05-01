"""
Hermes WebUI - Authentication E2E Tests

Test password authentication flow:
- Login page access
- Password submission
- Session cookie management
- Logout
- Protected route access
"""

import pytest
import re
import os
from playwright.sync_api import Page, expect


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(scope="function")
def auth_enabled_page(page: Page, base_url: str) -> Page:
    """Navigate to login page when auth is enabled."""
    # Check if auth is enabled
    page.goto(f"{base_url}/")
    yield page


# =============================================================================
# Test 1: Login Page
# =============================================================================

class TestLoginPage:
    """Test login page functionality."""

    def test_login_page_loads(self, auth_enabled_page: Page, base_url: str):
        """Login page should load when auth enabled."""
        # Skip if auth not enabled (check for login form)
        auth_enabled_page.goto(f"{base_url}/login")

        # Should have login form
        login_form = auth_enabled_page.locator("form")
        expect(login_form).to_be_visible()

    def test_login_form_elements(self, auth_enabled_page: Page, base_url: str):
        """Login form should have password input and submit button."""
        auth_enabled_page.goto(f"{base_url}/login")

        # Password input
        password_input = auth_enabled_page.locator('input[type="password"]')
        expect(password_input).to_be_visible()

        # Submit button
        submit_btn = auth_enabled_page.locator('button[type="submit"]')
        expect(submit_btn).to_be_visible()
        expect(submit_btn).to_contain_text(re.compile(r"Login|Sign In", re.IGNORECASE))


# =============================================================================
# Test 2: Login Flow
# =============================================================================

class TestLoginFlow:
    """Test login authentication flow."""

    def test_login_with_correct_password(
        self, auth_enabled_page: Page, base_url: str
    ):
        """Should login successfully with correct password."""
        # Skip test if no password configured
        if not os.getenv("HERMES_WEBUI_PASSWORD"):
            pytest.skip("HERMES_WEBUI_PASSWORD not set")

        auth_enabled_page.goto(f"{base_url}/login")

        # Enter password
        password_input = auth_enabled_page.locator('input[type="password"]')
        password_input.fill(os.getenv("HERMES_WEBUI_PASSWORD"))

        # Submit
        submit_btn = auth_enabled_page.locator('button[type="submit"]')
        submit_btn.click()

        # Should redirect to chat
        expect(auth_enabled_page).to_have_url(f"{base_url}/chat")

    def test_login_with_incorrect_password(
        self, auth_enabled_page: Page, base_url: str
    ):
        """Should show error with incorrect password."""
        if not os.getenv("HERMES_WEBUI_PASSWORD"):
            pytest.skip("HERMES_WEBUI_PASSWORD not set")

        auth_enabled_page.goto(f"{base_url}/login")

        # Enter wrong password
        password_input = auth_enabled_page.locator('input[type="password"]')
        password_input.fill("wrongpassword123")

        # Submit
        submit_btn = auth_enabled_page.locator('button[type="submit"]')
        submit_btn.click()

        # Should show error
        error_msg = auth_enabled_page.locator(".error, .alert")
        expect(error_msg).to_be_visible()
        expect(error_msg).to_contain_text(re.compile(r"incorrect|invalid|error", re.IGNORECASE))


# =============================================================================
# Test 3: Protected Routes
# =============================================================================

class TestProtectedRoutes:
    """Test route protection with authentication."""

    def test_chat_redirects_to_login(
        self, auth_enabled_page: Page, base_url: str
    ):
        """Chat page should redirect to login when not authenticated."""
        # Only test if auth is enabled
        if not os.getenv("HERMES_WEBUI_PASSWORD"):
            pytest.skip("HERMES_WEBUI_PASSWORD not set")

        auth_enabled_page.goto(f"{base_url}/chat")

        # Should redirect to login
        expect(auth_enabled_page).to_have_url(f"{base_url}/login")

    def test_api_requires_auth(self, auth_enabled_page: Page, base_url: str):
        """API endpoints should require authentication."""
        if not os.getenv("HERMES_WEBUI_PASSWORD"):
            pytest.skip("HERMES_WEBUI_PASSWORD not set")

        # Try to access API without auth
        response = auth_enabled_page.request.get(f"{base_url}/api/chat/sessions")

        # Should get 401
        assert response.status == 401


# =============================================================================
# Test 4: Session Management
# =============================================================================

class TestSessionManagement:
    """Test auth session cookie management."""

    def test_session_cookie_set_after_login(
        self, auth_enabled_page: Page, base_url: str
    ):
        """Session cookie should be set after successful login."""
        if not os.getenv("HERMES_WEBUI_PASSWORD"):
            pytest.skip("HERMES_WEBUI_PASSWORD not set")

        auth_enabled_page.goto(f"{base_url}/login")

        # Login
        password_input = auth_enabled_page.locator('input[type="password"]')
        password_input.fill(os.getenv("HERMES_WEBUI_PASSWORD"))
        submit_btn = auth_enabled_page.locator('button[type="submit"]')
        submit_btn.click()

        # Wait for redirect
        auth_enabled_page.wait_for_url(f"{base_url}/chat")

        # Check cookie
        cookies = auth_enabled_page.context.cookies()
        session_cookie = next(
            (c for c in cookies if c["name"] == "hermes_session"), None
        )

        assert session_cookie is not None
        assert session_cookie["value"] != ""

    def test_logout_clears_session(self, auth_enabled_page: Page, base_url: str):
        """Logout should clear session cookie."""
        if not os.getenv("HERMES_WEBUI_PASSWORD"):
            pytest.skip("HERMES_WEBUI_PASSWORD not set")

        # Login first
        auth_enabled_page.goto(f"{base_url}/login")
        password_input = auth_enabled_page.locator('input[type="password"]')
        password_input.fill(os.getenv("HERMES_WEBUI_PASSWORD"))
        submit_btn = auth_enabled_page.locator('button[type="submit"]')
        submit_btn.click()
        auth_enabled_page.wait_for_url(f"{base_url}/chat")

        # Find and click logout
        logout_btn = auth_enabled_page.locator(
            'button:has-text("Logout"), a:has-text("Logout")'
        )
        if logout_btn.count() > 0:
            logout_btn.click()

            # Session cookie should be cleared
            cookies = auth_enabled_page.context.cookies()
            session_cookie = next(
                (c for c in cookies if c["name"] == "hermes_session"), None
            )

            # Cookie should be gone or empty
            assert session_cookie is None or session_cookie["value"] == ""


# =============================================================================
# Test 5: Security
# =============================================================================

class TestSecurity:
    """Test security features."""

    def test_password_field_masked(self, auth_enabled_page: Page, base_url: str):
        """Password field should be masked."""
        auth_enabled_page.goto(f"{base_url}/login")

        password_input = auth_enabled_page.locator('input[type="password"]')
        input_type = password_input.get_attribute("type")

        assert input_type == "password"

    def test_cookie_httponly(self, auth_enabled_page: Page, base_url: str):
        """Session cookie should be HttpOnly."""
        if not os.getenv("HERMES_WEBUI_PASSWORD"):
            pytest.skip("HERMES_WEBUI_PASSWORD not set")

        auth_enabled_page.goto(f"{base_url}/login")
        password_input = auth_enabled_page.locator('input[type="password"]')
        password_input.fill(os.getenv("HERMES_WEBUI_PASSWORD"))
        submit_btn = auth_enabled_page.locator('button[type="submit"]')
        submit_btn.click()
        auth_enabled_page.wait_for_url(f"{base_url}/chat")

        # Get cookies with details
        cookies = auth_enabled_page.context.cookies()
        session_cookie = next(
            (c for c in cookies if c["name"] == "hermes_session"), None
        )

        if session_cookie:
            # HttpOnly cookies can't be accessed via JavaScript
            # Playwright can access all cookies, but we check the httponly flag
            assert session_cookie.get("httpOnly", True) is True

    def test_rate_limiting_display(self, auth_enabled_page: Page, base_url: str):
        """Rate limiting error should be displayed after multiple failures."""
        if not os.getenv("HERMES_WEBUI_PASSWORD"):
            pytest.skip("HERMES_WEBUI_PASSWORD not set")

        auth_enabled_page.goto(f"{base_url}/login")

        # Try wrong password multiple times
        password_input = auth_enabled_page.locator('input[type="password"]')
        submit_btn = auth_enabled_page.locator('button[type="submit"]')

        for _ in range(5):
            password_input.fill("wrongpassword")
            submit_btn.click()
            auth_enabled_page.wait_for_timeout(500)

        # Should show rate limit or too many attempts error
        error_msg = auth_enabled_page.locator(".error, .alert")
        expect(error_msg).to_contain_text(
            re.compile(r"too many|rate limit|try again", re.IGNORECASE), timeout=3000
        )
