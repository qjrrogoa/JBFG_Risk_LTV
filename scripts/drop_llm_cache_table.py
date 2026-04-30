import argparse
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from sqlalchemy import text
try:
    from backend.database import drop_legacy_llm_cache, engine
except ModuleNotFoundError:
    BACKEND_DIR = os.path.join(PROJECT_ROOT, "backend")
    if BACKEND_DIR not in sys.path:
        sys.path.append(BACKEND_DIR)
    from database import drop_legacy_llm_cache, engine


def main() -> None:
    parser = argparse.ArgumentParser(description="Drop legacy llm_cache table from DB.")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Delete llm_cache table without confirmation."
    )
    args = parser.parse_args()

    with engine.begin() as conn:
        exists = conn.execute(text("SELECT to_regclass('public.llm_cache')")).scalar()

    if not exists:
        print("[migration] llm_cache table does not exist. Nothing to do.")
        return

    if not args.yes:
        raise SystemExit(
            "[migration] llm_cache table exists. "
            "Run with --yes to drop it (예: python scripts/drop_llm_cache_table.py --yes)"
        )

    drop_legacy_llm_cache()
    print("[migration] llm_cache table dropped.")


if __name__ == "__main__":
    main()
