"""Document Intelligence Layer — unified document normalization pipeline.

All document understanding goes through this module. Gateway platforms
(Telegram, Discord, etc.) only handle *receiving* files; parsing and
normalization happen here so the logic is reusable across every platform.
"""

from agent.document_processing.router import process_document  # noqa: F401
from agent.document_processing.types import DocumentResult  # noqa: F401
