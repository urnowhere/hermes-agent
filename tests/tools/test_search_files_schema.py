"""Tests for the search_files tool schema."""

from tools.file_tools import SEARCH_FILES_SCHEMA


def test_search_files_schema_exposes_include_tcc_paths_override():
    prop = SEARCH_FILES_SCHEMA["parameters"]["properties"]["include_tcc_paths"]

    assert prop["type"] == "boolean"
    assert prop["default"] is False
    assert "macOS" in prop["description"]
    assert "TCC" in prop["description"]
