"""region_auction_records 의 region_file / province 표준화 백필.

과거 적재 스크립트가 CSV 파일명을 그대로 region_file 로 저장해
임시 파일(_new.csv)에서 온 행이 '서울_new' 처럼 남았고,
'전남광주' 검색 지역은 시도 자체가 표준 시도명이 아니라 집계에서 '경기'로 잡혔다.

    python3 scripts/backfill_region_file.py            # 변경 예정 건수만 출력
    python3 scripts/backfill_region_file.py --apply    # 실제 반영
"""

import argparse
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
BACKEND_DIR = os.path.join(PROJECT_ROOT, "backend")
for p in (PROJECT_ROOT, BACKEND_DIR, SCRIPT_DIR):
    if p not in sys.path:
        sys.path.append(p)

from sqlalchemy import text

from database import engine
from region_names import CANONICAL_PROVINCES

# 1) '전남광주' 합본 시도 분리 (광주광역시는 자치구, 전남은 시·군만 존재)
SPLIT_COMBINED = """
UPDATE region_auction_records
   SET province    = CASE WHEN COALESCE(district, '') LIKE '%%구' THEN '광주' ELSE '전남' END,
       region_file = CASE WHEN COALESCE(district, '') LIKE '%%구' THEN '광주' ELSE '전남' END,
       address     = CASE
                       WHEN address LIKE '전남광주%%'
                       THEN (CASE WHEN COALESCE(district, '') LIKE '%%구' THEN '광주' ELSE '전남' END)
                            || substring(address from length('전남광주') + 1)
                       ELSE address
                     END
 WHERE province = '전남광주'
"""

# 2) region_file 의 임시 접미사(_new) 제거
STRIP_NEW_SUFFIX = """
UPDATE region_auction_records
   SET region_file = left(region_file, length(region_file) - 4)
 WHERE region_file LIKE '%%\\_new'
   AND left(region_file, length(region_file) - 4) = ANY(:canonical)
"""

COUNT_COMBINED = "SELECT count(*) FROM region_auction_records WHERE province = '전남광주'"
COUNT_NEW = r"SELECT count(*) FROM region_auction_records WHERE region_file LIKE '%%\_new'"


def report(conn, label):
    print(f"\n--- {label} ---")
    print(f"province='전남광주'  : {conn.execute(text(COUNT_COMBINED)).scalar():,}건")
    print(f"region_file '%_new' : {conn.execute(text(COUNT_NEW)).scalar():,}건")
    bad = conn.execute(
        text(
            "SELECT region_file, count(*) FROM region_auction_records "
            "WHERE region_file <> ALL(:canonical) GROUP BY 1 ORDER BY 2 DESC"
        ),
        {"canonical": CANONICAL_PROVINCES},
    ).fetchall()
    print(f"표준 지역명이 아닌 region_file: {len(bad)}종 {[tuple(r) for r in bad]}")


def main():
    parser = argparse.ArgumentParser(description="region_file / province 표준화 백필")
    parser.add_argument("--apply", action="store_true", help="실제 UPDATE 실행 (미지정 시 현황만 출력)")
    args = parser.parse_args()

    with engine.connect() as conn:
        report(conn, "BEFORE")

    if not args.apply:
        print("\n(dry-run) 반영하려면 --apply 를 붙여 다시 실행하세요.")
        return

    with engine.begin() as conn:
        n1 = conn.execute(text(SPLIT_COMBINED)).rowcount
        print(f"\n[1] '전남광주' 분리: {n1:,}건")
        n2 = conn.execute(text(STRIP_NEW_SUFFIX), {"canonical": CANONICAL_PROVINCES}).rowcount
        print(f"[2] '_new' 접미사 제거: {n2:,}건")

    with engine.connect() as conn:
        report(conn, "AFTER")


if __name__ == "__main__":
    main()
