"""Enhanced error handling with better error messages and recovery."""

from typing import Optional, Type
from .errors import RuntimeAPIError, TimeoutError as FormalCCTimeoutError


class ErrorHandler:
    """Handles errors with user-friendly messages and recovery suggestions."""

    @staticmethod
    def get_user_friendly_message(error: Exception) -> str:
        """Convert technical error to user-friendly message."""
        if isinstance(error, RuntimeAPIError):
            if error.status_code == 401:
                return (
                    "Authentication failed. Please check:\n"
                    "  1. Your API key is set correctly in $FORMALCC_API_KEY\n"
                    "  2. The API key format is valid (fsy_live_* or fsy_test_*)\n"
                    "  3. The API key has not expired"
                )
            elif error.status_code == 403:
                return (
                    "Access forbidden. Please check:\n"
                    "  1. Your API key has permission to access this workspace\n"
                    "  2. The workspace_id is correct\n"
                    "  3. Your tenant_id is configured correctly"
                )
            elif error.status_code == 404:
                return (
                    "Resource not found. Please check:\n"
                    "  1. The base_url is correct\n"
                    "  2. The workspace_id exists\n"
                    "  3. The API endpoint is available"
                )
            elif error.status_code == 429:
                return (
                    "Rate limit exceeded. Please:\n"
                    "  1. Wait a few minutes before retrying\n"
                    "  2. Reduce request frequency\n"
                    "  3. Contact support if this persists"
                )
            elif error.status_code == 503:
                return (
                    "Service temporarily unavailable. Please:\n"
                    "  1. Wait a few minutes and retry\n"
                    "  2. Check status at https://status.formsy.ai\n"
                    "  3. Contact support if this persists"
                )
            elif error.status_code and error.status_code >= 500:
                return (
                    "Server error occurred. Please:\n"
                    "  1. Retry your request\n"
                    "  2. Check if the issue persists\n"
                    "  3. Contact support with the error details"
                )

        elif isinstance(error, FormalCCTimeoutError):
            return (
                "Request timed out. Please:\n"
                "  1. Check your network connection\n"
                "  2. Increase timeout_s in configuration\n"
                "  3. Verify the base_url is reachable"
            )

        elif isinstance(error, ConnectionError):
            return (
                "Connection failed. Please check:\n"
                "  1. Your network connection\n"
                "  2. The base_url is correct and reachable\n"
                "  3. Firewall settings allow outbound HTTPS"
            )

        return f"An error occurred: {str(error)}"

    @staticmethod
    def get_recovery_suggestions(error: Exception) -> list[str]:
        """Get recovery suggestions for an error."""
        suggestions = []

        if isinstance(error, RuntimeAPIError):
            if error.status_code == 401:
                suggestions.extend([
                    "Run 'hermes formalcc-memory validate' to check your configuration",
                    "Verify your API key with 'echo $FORMALCC_API_KEY'",
                    "Generate a new API key from the FormalCC dashboard",
                ])
            elif error.status_code == 503:
                suggestions.extend([
                    "Wait 60 seconds and retry",
                    "Check service status at https://status.formsy.ai",
                    "Use a different gateway URL if available",
                ])
            elif error.status_code and error.status_code >= 500:
                suggestions.extend([
                    "Retry the operation",
                    "Check if the issue is transient",
                    "Contact support if it persists",
                ])

        elif isinstance(error, FormalCCTimeoutError):
            suggestions.extend([
                "Increase timeout: export FORMALCC_TIMEOUT=60",
                "Check network latency to the API",
                "Try a different network connection",
            ])

        return suggestions

    @classmethod
    def handle_error(
        cls,
        error: Exception,
        context: Optional[str] = None,
        raise_error: bool = False
    ) -> None:
        """Handle error with user-friendly output."""
        import logging
        logger = logging.getLogger("formalcc.error_handler")

        if context:
            print(f"\n✗ Error during {context}:")
        else:
            print("\n✗ Error:")

        print(cls.get_user_friendly_message(error))

        suggestions = cls.get_recovery_suggestions(error)
        if suggestions:
            print("\nSuggested actions:")
            for i, suggestion in enumerate(suggestions, 1):
                print(f"  {i}. {suggestion}")

        logger.error(f"Error: {error}", exc_info=True)

        if raise_error:
            raise


class ErrorRecovery:
    """Provides error recovery strategies."""

    @staticmethod
    def should_retry(error: Exception, attempt: int, max_attempts: int) -> bool:
        """Determine if error is retryable."""
        if attempt >= max_attempts:
            return False

        # Retry on transient errors
        if isinstance(error, FormalCCTimeoutError):
            return True

        if isinstance(error, RuntimeAPIError):
            # Retry on 5xx errors and 429
            if error.status_code in (429, 500, 502, 503, 504):
                return True

        if isinstance(error, ConnectionError):
            return True

        return False

    @staticmethod
    def get_retry_delay(error: Exception, attempt: int) -> float:
        """Get delay before retry based on error type."""
        if isinstance(error, RuntimeAPIError) and error.status_code == 429:
            # Rate limit: longer delay
            return min(60.0, 5.0 * (2 ** attempt))

        # Default exponential backoff
        return min(30.0, 1.0 * (2 ** attempt))
