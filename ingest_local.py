from __future__ import annotations

import sys

from src.config import load_settings
from src.local_ingester import LocalIngester


def main() -> None:
    settings = load_settings()
    LocalIngester(settings).run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted by user.")
        sys.exit(1)
    except Exception as ex:
        print(f"ERROR: {ex}")
        sys.exit(1)
