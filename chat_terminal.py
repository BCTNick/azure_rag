from __future__ import annotations

import sys

from src.chat import ChatQueryEngine
from src.config import load_settings


def main() -> None:
    settings = load_settings()
    chat_query_engine = ChatQueryEngine(settings, enable_trace_log=True)
    chat_query_engine.chat_in_terminal()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted by user.")
        sys.exit(1)
    except Exception as ex:
        print(f"ERROR: {ex}")
        sys.exit(1)
