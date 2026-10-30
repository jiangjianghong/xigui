"""Initialize/reset the administrator: uv run python scripts/set_admin_password.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.admin_password import main  # noqa: E402


if __name__ == "__main__":
    main()
