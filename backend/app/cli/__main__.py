"""`python -m app.cli` (development). Installed launchers use LAUNCH instead, see below."""
import sys

# The user's project folder is the current directory and may contain its own `app/` package: never import from it.
if sys.path and sys.path[0] in ("", "."):
    sys.path.pop(0)

from app.cli.app import main  # noqa: E402

raise SystemExit(main())
