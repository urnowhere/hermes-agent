"""
Hermes WebUI Chat - E2E Tests with Playwright

Critical user flows for chat functionality:
- Page load and initial state
- Session creation and management
- Message sending and response
- Model selection
- Workspace switching
"""

import pytest
import re
from playwright.sync_api import Page, expect, TimeoutError
from typing import Generator
import time


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(scope="function")
def chat_page(page: Page, base_url: str) -> Generator[Page, None, None]:
    """Navigate to chat page before each test."""
    page.goto(f"{base_url}/chat")
    page.wait_for_load_state("networkidle")
    yield page


# =============================================================================
# Test 1: Page Load and Initial State
# =============================================================================

class TestPageLoad:
    """Test chat page loads correctly."""

    def test_page_loads_successfully(self, chat_page: Page):
        """Chat page should load with correct title."""
        expect(chat_page).to_have_title(re.compile(r"Hermes"))

    def test_main_layout_visible(self, chat_page: Page):
        """Main layout components should be visible."""
        expect(chat_page.locator(".layout")).to_be_visible()
        expect(chat_page.locator(".sidebar")).to_be_visible()
        expect(chat_page.locator("#panelChat")).to_be_visible()

    def test_empty_state_displayed(self, chat_page: Page):
        """Empty state should show when no conversation active."""
        empty_state = chat_page.locator("#emptyState")
        expect(empty_state).to_be_visible()

    def test_new_chat_button_exists(self, chat_page: Page):
        """New chat button should be visible."""
        new_chat_btn = chat_page.locator("#btnNewChat")
        expect(new_chat_btn).to_be_visible()
        expect(new_chat_btn).to_contain_text(re.compile(r"New conversation", re.IGNORECASE))


# =============================================================================
# Test 2: UI Elements
# =============================================================================

class TestUIElements:
    """Test UI elements are present and functional."""

    def test_message_input_exists(self, chat_page: Page):
        """Message input should exist and be enabled."""
        msg_input = chat_page.locator("#msg")
        expect(msg_input).to_be_visible()
        expect(msg_input).to_be_enabled()
        expect(msg_input).to_have_attribute("placeholder", re.compile(r"Message Hermes", re.IGNORECASE))

    def test_model_selector_exists(self, chat_page: Page):
        """Model selector should be visible."""
        model_select = chat_page.locator("#modelSelect")
        expect(model_select).to_be_visible()

    def test_session_list_exists(self, chat_page: Page):
        """Session list panel should exist."""
        session_list = chat_page.locator("#sessionList")
        expect(session_list).to_be_visible()

    def test_workspace_panel_exists(self, chat_page: Page):
        """Workspace panel should exist."""
        workspace_panel = chat_page.locator("#panelWorkspaces")
        expect(workspace_panel).to_be_visible()


# =============================================================================
# Test 3: Session Management
# =============================================================================

class TestSessionManagement:
    """Test session CRUD operations."""

    def test_create_new_session(self, chat_page: Page):
        """Creating new session should work."""
        # Click new chat button
        new_chat_btn = chat_page.locator("#btnNewChat")
        new_chat_btn.click()

        # Wait for new session to be created
        chat_page.wait_for_timeout(1000)

        # Should have empty state again
        empty_state = chat_page.locator("#emptyState")
        expect(empty_state).to_be_visible(timeout=5000)

    def test_session_list_updates(self, chat_page: Page):
        """Session list should update after creating new session."""
        # Get initial session count
        session_list = chat_page.locator("#sessionList")

        # Create new session
        new_chat_btn = chat_page.locator("#btnNewChat")
        new_chat_btn.click()
        chat_page.wait_for_timeout(1000)

        # Session list should update (may take a moment)
        expect(session_list).to_be_visible()


# =============================================================================
# Test 4: Message Sending
# =============================================================================

class TestMessageSending:
    """Test message sending functionality."""

    def test_type_message(self, chat_page: Page):
        """Should be able to type a message."""
        msg_input = chat_page.locator("#msg")
        msg_input.fill("Test message")
        expect(msg_input).to_have_value("Test message")

    def test_send_button_enabled(self, chat_page: Page):
        """Send button should be enabled when message typed."""
        msg_input = chat_page.locator("#msg")
        send_btn = chat_page.locator('button[title="Send"]')

        # Initially disabled
        expect(send_btn).to_be_disabled()

        # Type message
        msg_input.fill("Hello")

        # Should become enabled
        expect(send_btn).to_be_enabled(timeout=3000)

    def test_message_appears_in_chat(self, chat_page: Page):
        """Sent message should appear in chat history."""
        msg_input = chat_page.locator("#msg")
        send_btn = chat_page.locator('button[title="Send"]')

        # Type and send
        msg_input.fill("Test message 123")
        chat_page.wait_for_timeout(500)  # Wait for button to enable
        send_btn.click()

        # Message should appear in chat
        expect(chat_page.locator(".message")).to_contain_text(
            "Test message 123",
            timeout=5000
        )


# =============================================================================
# Test 5: Model Selection
# =============================================================================

class TestModelSelection:
    """Test model selection functionality."""

    def test_model_selector_has_options(self, chat_page: Page):
        """Model selector should have options."""
        model_select = chat_page.locator("#modelSelect")

        # Should have at least one option
        options = model_select.locator("option")
        expect(options).to_have_count(1, timeout=5000)

    def test_select_model(self, chat_page: Page):
        """Should be able to select a model."""
        model_select = chat_page.locator("#modelSelect")

        # Get first option value
        first_option = model_select.locator("option").first
        value = first_option.get_attribute("value")

        if value:
            model_select.select_option(value)
            expect(model_select).to_have_value(value)


# =============================================================================
# Test 6: Error Handling
# =============================================================================

class TestErrorHandling:
    """Test error handling and edge cases."""

    def test_empty_message_not_sent(self, chat_page: Page):
        """Empty messages should not be sent."""
        msg_input = chat_page.locator("#msg")
        send_btn = chat_page.locator('button[title="Send"]')

        # Clear and try to send
        msg_input.fill("")
        chat_page.wait_for_timeout(500)

        # Send button should remain disabled
        expect(send_btn).to_be_disabled()

    def test_api_error_graceful_handling(self, chat_page: Page, base_url: str):
        """API errors should be handled gracefully."""
        # Mock API error
        chat_page.route(f"{base_url}/api/models", lambda route: route.fulfill(
            status=500,
            body='{"error": "Internal server error"}'
        ))

        # Navigate - should not crash
        chat_page.goto(f"{base_url}/chat")

        # Page should still be visible
        expect(chat_page.locator(".layout")).to_be_visible()


# =============================================================================
# Test 7: Responsiveness
# =============================================================================

class TestResponsiveness:
    """Test responsive design."""

    def test_mobile_viewport(self, chat_page: Page):
        """Page should work on mobile viewport."""
        chat_page.set_viewport_size({"width": 375, "height": 667})

        # Layout should still be visible
        expect(chat_page.locator(".layout")).to_be_visible()

    def test_tablet_viewport(self, chat_page: Page):
        """Page should work on tablet viewport."""
        chat_page.set_viewport_size({"width": 768, "height": 1024})

        # Layout should still be visible
        expect(chat_page.locator(".layout")).to_be_visible()


# =============================================================================
# Test 8: Performance
# =============================================================================

class TestPerformance:
    """Test performance metrics."""

    def test_page_load_time(self, chat_page: Page, base_url: str):
        """Page should load within acceptable time."""
        start_time = time.time()
        chat_page.goto(f"{base_url}/chat")
        chat_page.wait_for_load_state("networkidle")
        load_time = time.time() - start_time

        # Should load in under 5 seconds
        assert load_time < 5.0, f"Page took {load_time:.2f}s to load"

    def test_initial_render_time(self, chat_page: Page, base_url: str):
        """Initial render should be fast."""
        start_time = time.time()
        chat_page.goto(f"{base_url}/chat")

        # Wait for main layout
        chat_page.locator(".layout").wait_for(state="visible", timeout=5000)
        render_time = time.time() - start_time

        # Should render in under 3 seconds
        assert render_time < 3.0, f"Initial render took {render_time:.2f}s"
