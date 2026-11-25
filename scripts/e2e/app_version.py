from pathlib import Path


def version() -> str:
    """The release version: the repository's VERSION file is the one place it is decided (packaging/version.py)."""
    f = Path(__file__).resolve().parents[2] / "VERSION"
    return f.read_text().strip() if f.exists() else "?"
