import argparse
import os
import sys

from sqlalchemy import text

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
BACKEND_DIR = os.path.join(PROJECT_ROOT, "backend")

if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)
if BACKEND_DIR not in sys.path:
    sys.path.append(BACKEND_DIR)

try:
    from database import engine
except Exception:
    from sqlalchemy import create_engine

    def _load_env_file(path: str):
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key not in os.environ:
                    os.environ[key.strip()] = value.strip().strip('"').strip("'")

    _load_env_file(os.path.join(PROJECT_ROOT, ".env"))

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("[error] DATABASE_URL이 없습니다. .env 또는 환경변수에서 DATABASE_URL을 설정해 주세요.")

    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    engine = create_engine(database_url, pool_pre_ping=True)


def _ensure_table_exists(conn):
    exists_sql = text(
        """
        SELECT to_regclass('public.region_auction_records') IS NOT NULL
        """
    )
    exists = bool(conn.execute(exists_sql).scalar())
    if not exists:
        raise SystemExit("[error] region_auction_records 테이블이 없습니다.")


def _count_duplicate_groups(conn):
    dup_sql = text(
        """
        SELECT COUNT(*)
        FROM (
            SELECT
                region_file,
                case_number,
                auction_date::date,
                usage,
                COUNT(*) AS cnt
            FROM region_auction_records
            GROUP BY region_file, case_number, auction_date::date, usage
            HAVING COUNT(*) > 1
        ) AS dups
        """
    )
    return conn.execute(dup_sql).scalar() or 0


def _dedupe_region_auction_records(conn, dry_run=True):
    duplicate_groups = _count_duplicate_groups(conn)
    print(f"[dup-check] duplicate groups = {duplicate_groups}")

    sample_sql = text(
        """
        SELECT
            region_file,
            case_number,
            auction_date::date AS auction_date,
            usage,
            COUNT(*) AS cnt
        FROM region_auction_records
        GROUP BY region_file, case_number, auction_date::date, usage
        HAVING COUNT(*) > 1
        ORDER BY cnt DESC, region_file, case_number
        LIMIT 20
        """
    )
    rows = conn.execute(sample_sql).fetchall()
    if rows:
        print("[dup-check] top duplicated keys (up to 20)")
        for row in rows:
            print(
                f"  - region_file={row.region_file}, case_number={row.case_number}, "
                f"auction_date={row.auction_date}, usage={row.usage}, count={row.cnt}"
            )

    if duplicate_groups == 0:
        print("[dup-check] 중복 없음. 정리 불필요.")
        return 0

    if dry_run:
        print("[dry-run] 중복 삭제를 실행하지 않았습니다. --delete를 추가하면 실제 삭제를 수행합니다.")
        return 0

    delete_sql = text(
        """
        WITH ranked AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY region_file, case_number, auction_date::date, usage
                    ORDER BY id DESC
                ) AS rn
            FROM region_auction_records
            WHERE case_number IS NOT NULL
        )
        DELETE FROM region_auction_records
        WHERE id IN (
            SELECT id FROM ranked WHERE rn > 1
        )
        """
    )
    result = conn.execute(delete_sql)
    return result.rowcount or 0


def main():
    parser = argparse.ArgumentParser(
        description="Detect and optionally remove duplicates in region_auction_records."
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Actually delete duplicates after check. Without this flag, only dry-run.",
    )
    args = parser.parse_args()

    with engine.begin() as conn:
        _ensure_table_exists(conn)
        deleted = _dedupe_region_auction_records(conn, dry_run=not args.delete)
        if args.delete:
            print(f"[dedupe] deleted rows: {deleted}")
            print(f"[dedupe] remaining duplicate groups: {_count_duplicate_groups(conn)}")


if __name__ == "__main__":
    main()
