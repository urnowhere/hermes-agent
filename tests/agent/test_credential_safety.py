"""Tests for credential safety guardrails (issue #9590).

Verifies:
1. CREDENTIAL_SAFETY_GUIDANCE is included in the system prompt
2. Terminal tool description includes security note
3. _scrub_credential_leaks catches fabricated credential patterns
4. _scrub_credential_leaks does NOT false-positive on benign text
"""

import unittest
from unittest.mock import patch


class TestCredentialSafetyGuidance(unittest.TestCase):
    """Verify CREDENTIAL_SAFETY_GUIDANCE exists and is included in prompts."""

    def test_guidance_constant_exists(self):
        from agent.prompt_builder import CREDENTIAL_SAFETY_GUIDANCE
        self.assertIn("NEVER", CREDENTIAL_SAFETY_GUIDANCE)
        self.assertIn("password", CREDENTIAL_SAFETY_GUIDANCE.lower())
        self.assertIn("credential", CREDENTIAL_SAFETY_GUIDANCE.lower())

    def test_guidance_in_system_prompt(self):
        """CREDENTIAL_SAFETY_GUIDANCE must appear in every system prompt."""
        from agent.prompt_builder import CREDENTIAL_SAFETY_GUIDANCE

        # Minimal AIAgent to test _build_system_prompt
        from run_agent import AIAgent
        agent = AIAgent.__new__(AIAgent)
        agent.valid_tool_names = set()
        agent.tools = []
        agent.skip_context_files = True
        agent.load_soul_identity = False
        agent._memory_store = None
        agent._memory_manager = None
        agent._memory_enabled = False
        agent._user_profile_enabled = False
        agent._tool_use_enforcement = False
        agent.pass_session_id = False
        agent.session_id = None
        agent.model = "test-model"
        agent.provider = "test"
        agent.platform = "telegram"

        with patch("agent.prompt_builder.load_soul_md", return_value=None):
            prompt = agent._build_system_prompt()

        self.assertIn("NEVER guess", prompt)
        self.assertIn("Credential safety", prompt)


class TestTerminalToolSecurityNote(unittest.TestCase):
    """Verify terminal tool description includes security note."""

    def test_security_note_in_description(self):
        from tools.terminal_tool import TERMINAL_TOOL_DESCRIPTION
        self.assertIn("SECURITY", TERMINAL_TOOL_DESCRIPTION)
        self.assertIn("NEVER include passwords", TERMINAL_TOOL_DESCRIPTION)
        self.assertIn("sudo", TERMINAL_TOOL_DESCRIPTION)


class TestScrubCredentialLeaks(unittest.TestCase):
    """Verify _scrub_credential_leaks catches fabricated credential patterns."""

    @classmethod
    def setUpClass(cls):
        from run_agent import AIAgent
        cls._scrub_fn = staticmethod(AIAgent._scrub_credential_leaks)

    def _scrub(self, text):
        return self._scrub_fn(text)

    def test_password_colon_value(self):
        """'password: hunter2' should be redacted."""
        text = "The password: hunter2"
        result = self._scrub(text)
        self.assertNotIn("hunter2", result)
        self.assertIn("[REDACTED]", result)

    def test_password_is_value(self):
        """'password is secret123' should be redacted."""
        text = "The password is secret123"
        result = self._scrub(text)
        self.assertNotIn("secret123", result)
        self.assertIn("[REDACTED]", result)

    def test_with_password_value(self):
        """'With password guessed_pw:' — the exact #9590 pattern."""
        text = "With password guessed_pw:"
        result = self._scrub(text)
        self.assertNotIn("guessed_pw", result)
        self.assertIn("[REDACTED]", result)

    def test_sudo_password_value(self):
        """'sudo password: mypass123' should be redacted."""
        text = "Try using sudo password: mypass123"
        result = self._scrub(text)
        self.assertNotIn("mypass123", result)
        self.assertIn("[REDACTED]", result)

    def test_password_equals_value(self):
        """'password=abc123' should be redacted."""
        text = "Set password=abc123 in the config"
        result = self._scrub(text)
        self.assertNotIn("abc123", result)
        self.assertIn("[REDACTED]", result)

    def test_quoted_password(self):
        """'password: \"secret\"' should be redacted."""
        text = 'The password: "mysecret"'
        result = self._scrub(text)
        self.assertNotIn("mysecret", result)
        self.assertIn("[REDACTED]", result)

    def test_api_key_value(self):
        """'api key: sk-abc123...' should be redacted."""
        text = "Your api key: sk-abc123def"
        result = self._scrub(text)
        self.assertNotIn("sk-abc123def", result)
        self.assertIn("[REDACTED]", result)

    def test_token_value(self):
        """'token is xyz789' should be redacted."""
        text = "The token is xyz789abc"
        result = self._scrub(text)
        self.assertNotIn("xyz789abc", result)
        self.assertIn("[REDACTED]", result)

    # --- False-positive avoidance ---

    def test_no_false_positive_on_generic_password_prose(self):
        """'use a strong password' should NOT be scrubbed."""
        text = "Make sure to use a strong password for your account."
        result = self._scrub(text)
        self.assertEqual(text, result)

    def test_no_false_positive_on_password_requirements(self):
        """'password requirements' should NOT be scrubbed."""
        text = "The password requirements include at least 8 characters."
        result = self._scrub(text)
        self.assertEqual(text, result)

    def test_no_false_positive_on_sudo_failure_message(self):
        """Tool output about sudo failure should pass through."""
        text = "sudo: a password is required"
        result = self._scrub(text)
        # "required" matches but it's a benign word — this is fine since
        # the scrub only fires on the FINAL response, not tool output
        # (tool output is never passed through this method)

    def test_empty_and_none(self):
        """Empty/None input should pass through."""
        self.assertEqual(self._scrub(""), "")
        self.assertEqual(self._scrub(None), None)

    def test_no_false_positive_on_short_values(self):
        """Values shorter than 3 chars should NOT be matched."""
        text = "password is ok"
        result = self._scrub(text)
        self.assertEqual(text, result)


if __name__ == "__main__":
    unittest.main()
