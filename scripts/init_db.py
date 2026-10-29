from __future__ import annotations

from app.config import get_settings
from app.db import Database


def main() -> None:
    settings = get_settings()
    db = Database(settings.database_path)
    db.init()
    print(f"Initialised database at {settings.database_path}")


if __name__ == "__main__":
    main()
