"""Runtime configuration (env-overridable)."""
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("ZEUS_DATA_DIR", Path.home() / ".local" / "share" / "zeus"))
DB_PATH = Path(os.environ.get("ZEUS_DB", DATA_DIR / "zeus.db"))
DEFAULT_OLLAMA = os.environ.get("ZEUS_OLLAMA_URL", "http://localhost:11434")
HOST = os.environ.get("ZEUS_HOST", "127.0.0.1")
PORT = int(os.environ.get("ZEUS_PORT", "8765"))
MAX_AGENT_STEPS = int(os.environ.get("ZEUS_MAX_STEPS", "40"))
COMMAND_TIMEOUT = int(os.environ.get("ZEUS_CMD_TIMEOUT", "120"))
MAX_FILE_BYTES = 1_000_000
MAX_OUTPUT_CHARS = 20_000

VERSION = "0.2.0"
FOUNDER = "Ayrus498"
GITHUB_URL = "https://github.com/Ayrus498/zeus"
WEBSITE_URL = "https://zeus-agent.web.app/"
LICENSE_NAME = "MIT"
