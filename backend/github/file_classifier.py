from pathlib import Path
from enum import Enum
import re

class FileSignificance(Enum):
    CRITICAL = 7
    IMPORTANT = 6
    SUPPORTING = 5
    LOW_VALUE = 4
    GENERATED = 3
    VENDOR_DEPENDENCY = 2
    BINARY = 1
    UNKNOWN = 0

def get_file_significance(path: str) -> FileSignificance:
    p = Path(path)
    parts = [part.lower() for part in p.parts]
    filename = p.name.lower()
    suffix = p.suffix.lower()

    if any(part in {".git", "node_modules", "venv"} for part in parts):
        return FileSignificance.VENDOR_DEPENDENCY
        
    if any(part in {"dist", "build", "target", "coverage", "__pycache__"} for part in parts):
        return FileSignificance.GENERATED
        
    if "vendor" in parts or "third_party" in parts:
        return FileSignificance.VENDOR_DEPENDENCY
        
    if filename in {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "gemfile.lock"}:
        return FileSignificance.LOW_VALUE
        
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".mp4", ".mp3", ".wav", ".zip", ".tar", ".gz"}:
        return FileSignificance.BINARY
        
    if suffix in {".min.js", ".min.css"} or filename.endswith(".bundle.js"):
        return FileSignificance.GENERATED
        
    if filename in {"package.json", "dockerfile", "docker-compose.yml", "pyproject.toml", "cargo.toml", "go.mod", "tsconfig.json", "readme.md"}:
        return FileSignificance.IMPORTANT
        
    if "test" in parts or "tests" in parts or filename.startswith("test_") or filename.endswith("_test.py") or filename.endswith(".spec.ts"):
        return FileSignificance.SUPPORTING
        
    if "auth" in parts or "security" in parts or "payment" in parts or "api" in parts or "core" in parts:
        return FileSignificance.CRITICAL

    if suffix in {".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs", ".cpp", ".c", ".h", ".cs", ".rb", ".php", ".swift", ".kt", ".html", ".htm", ".css", ".scss", ".vue", ".svelte"}:
        return FileSignificance.IMPORTANT

    if suffix in {".md", ".yaml", ".yml", ".json", ".toml", ".ini", ".conf", ".sql", ".sh"}:
        return FileSignificance.SUPPORTING

    return FileSignificance.LOW_VALUE

def should_include_file(path: str) -> bool:
    """Determine if a file should be ingested/semantically analyzed based on path and significance."""
    sig = get_file_significance(path)
    return sig.value >= FileSignificance.SUPPORTING.value

def should_process_semantically(path: str) -> bool:
    """Determine if a file warrants full LLM semantic processing based on significance."""
    sig = get_file_significance(path)
    return sig.value >= FileSignificance.IMPORTANT.value

def detect_architectural_role(path: str) -> str:
    p = path.lower()
    if "test" in p or "spec" in p or "mock" in p: return "test"
    if "api" in p or "route" in p or "controller" in p or "endpoint" in p: return "api"
    if "db" in p or "database" in p or "model" in p or "schema" in p or "migration" in p: return "database"
    if "frontend" in p or "ui" in p or "components" in p or "views" in p or "pages" in p: return "frontend"
    if "backend" in p or "server" in p or "core" in p or "service" in p: return "backend"
    if "doc" in p or "readme" in p: return "docs"
    if "shared" in p or "common" in p or "utils" in p: return "shared"
    return "unknown"

def detect_file_type(path: str) -> str:
    p = Path(path)
    ext = p.suffix.lower()
    name = p.name.lower()
    if ext in {".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs", ".cpp", ".c", ".h", ".cs", ".rb", ".php", ".swift", ".kt"}:
        if "test" in name or "spec" in name: return "test"
        return "source"
    if name in {"package.json", "dockerfile", "docker-compose.yml", "pyproject.toml", "cargo.toml", "go.mod", "tsconfig.json"} or ext in {".yaml", ".yml", ".json", ".toml", ".ini", ".conf"}:
        return "config"
    if ext in {".md", ".txt", ".rst"}: return "documentation"
    if ext in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".mp4", ".mp3", ".wav", ".zip", ".tar", ".gz"}: return "asset"
    return "other"

def detect_language(path: str) -> str:
    ext_map = {
        ".py": "python", ".js": "javascript", ".ts": "typescript", ".jsx": "javascript", ".tsx": "typescript",
        ".java": "java", ".go": "go", ".rs": "rust", ".cpp": "cpp", ".c": "c", ".h": "c",
        ".cs": "csharp", ".rb": "ruby", ".php": "php", ".swift": "swift", ".kt": "kotlin",
        ".sh": "shell", ".sql": "sql", ".html": "html", ".css": "css"
    }
    return ext_map.get(Path(path).suffix.lower(), "unknown")
