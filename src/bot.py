"""Main trading loop scaffold."""

from __future__ import annotations

import time

from dotenv import load_dotenv


def main() -> None:
    """Load environment configuration and start the bot scaffold."""
    load_dotenv()
    print("Roostoo bot scaffold initialized. Strategy logic not implemented yet.")
    while False:
        time.sleep(1)


if __name__ == "__main__":
    main()
