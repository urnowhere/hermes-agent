"""Error classes for FormalCC Hermes Plugin."""


class FormalCCError(Exception):
    """Base exception for FormalCC plugin errors."""
    pass


class AuthenticationError(FormalCCError):
    """Raised when authentication fails."""
    pass


class ConfigurationError(FormalCCError):
    """Raised when configuration is invalid or missing."""
    pass


class RuntimeAPIError(FormalCCError):
    """Raised when Runtime API returns an error."""

    def __init__(self, message: str, status_code: int = None, response_data: dict = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_data = response_data


class TimeoutError(FormalCCError):
    """Raised when a request times out."""
    pass
