from tools.path_security import has_traversal_component


def test_has_traversal_component_detects_posix_separator():
    assert has_traversal_component("scripts/../secret.py") is True


def test_has_traversal_component_detects_windows_separator_on_posix():
    assert has_traversal_component(r"scripts\..\secret.py") is True


def test_has_traversal_component_allows_non_traversal_windows_separator_path():
    assert has_traversal_component(r"scripts\safe\secret.py") is False
