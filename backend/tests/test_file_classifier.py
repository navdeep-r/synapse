import pytest
from github.file_classifier import get_file_significance, FileSignificance, should_include_file

def test_file_significance():
    assert get_file_significance("backend/app/main.py") == FileSignificance.IMPORTANT
    assert get_file_significance("backend/tests/test_main.py") == FileSignificance.SUPPORTING
    assert get_file_significance("package.json") == FileSignificance.IMPORTANT
    assert get_file_significance("package-lock.json") == FileSignificance.LOW_VALUE
    assert get_file_significance("src/auth/service.ts") == FileSignificance.CRITICAL
    assert get_file_significance("dist/bundle.js") == FileSignificance.GENERATED
    assert get_file_significance("node_modules/express/index.js") == FileSignificance.VENDOR_DEPENDENCY
    assert get_file_significance("image.png") == FileSignificance.BINARY
    assert get_file_significance(".gitignore") == FileSignificance.LOW_VALUE

def test_should_include_file():
    assert should_include_file("backend/app/main.py") is True
    assert should_include_file("src/auth/service.ts") is True
    assert should_include_file("package-lock.json") is False
    assert should_include_file("dist/bundle.js") is False
    assert should_include_file("node_modules/express/index.js") is False
