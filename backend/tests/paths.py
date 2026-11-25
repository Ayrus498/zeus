"""Where things are, for tests in any subfolder (so moving a test file never breaks a path)."""
from pathlib import Path

TESTS = Path(__file__).resolve().parent
FIXTURES = TESTS / "fixtures"
BACKEND = TESTS.parent
REPO = BACKEND.parent
