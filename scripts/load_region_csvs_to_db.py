import argparse
import glob
import os
from sqlalchemy import text, inspect as sa_inspect

import pandas as pd

import sys
sys.path.append(os.path.join(os.getcwd(), "backend"))

from database import init_db, RegionAuctionRecord, SessionLocal


DATA_DIR = os.path.abspath(os.path.join(os.getcwd(), "data"))

EXCLUDE_FILES = {
    "LTV_기준(전북은행).csv",
    "LTV_기준(광주은행).csv",
    "양행별_용도_리스트.csv",
    "양행별_용도_리스트_최신.csv",
}

REQUIRED_COLUMNS = [
    "사건번호",
    "용도",
    "LTV_광주",
    "LTV_전북",
    "시도",
    "시군구",
    "소재지",
    "감정가",
    "최저가",
    "결과",
    "낙찰가",
    "낙찰율",
    "매각일",
    "분기",
    "기간구분",
]

def _to_float(value):
    if pd.isna(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    s = str(value).strip().replace(",", "").replace("%", "")
    if not s:
        return None

    try:
        return float(s)
    except ValueError:
        return None


def _to_datetime(value):
    if pd.isna(value):
        return None

    try:
        parsed = pd.to_datetime(value)
    except (TypeError, ValueError):
        return None

    if pd.isna(parsed):
        return None

    if hasattr(parsed, "to_pydatetime"):
        return parsed.to_pydatetime()
    return parsed


def _to_str(value):
    if pd.isna(value):
        return None
    s = str(value).strip()
    return s if s else None


def _ensure_region_usage_columns_as_text(db):
    # 과거 스키마 누락/타입 흔들림까지 보정
    # 1) 컬럼 존재 여부 확인
    existing = set()
    try:
        inspector = sa_inspect(db.get_bind())
        existing = {c["name"] for c in inspector.get_columns("region_auction_records")}
    except Exception:
        try:
            existing = {
                row[0]
                for row in db.execute(
                    text(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema = CURRENT_SCHEMA()
                          AND table_name = 'region_auction_records'
                        """
                    )
                ).fetchall()
            }
        except Exception:
            existing = set()

    # 2) legacy 이름 포함: jeonbuk, 전북 둘 다 보장
    for col in ["ltv_gwangju", "ltv_전북", "ltv_jeonbuk"]:
        if col not in existing:
            try:
                db.execute(text(f"ALTER TABLE region_auction_records ADD COLUMN {col} TEXT"))
            except Exception:
                pass
        try:
            db.execute(
                text(
                    f"ALTER TABLE region_auction_records "
                    f"ALTER COLUMN {col} TYPE TEXT USING {col}::text"
                )
            )
        except Exception:
            # sqlite 등 환경에서는 타입 변경 구문이 다를 수 있어 실패해도 진행
            pass


def _load_region_csv_files():
    files = []
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "*.csv"))):
        filename = os.path.basename(path)
        if filename in EXCLUDE_FILES:
            continue
        files.append(path)
    return files


def _read_csv_safe(path):
    encodings = ["utf-8-sig", "cp949"]
    for enc in encodings:
        try:
            return pd.read_csv(path, encoding=enc)
        except UnicodeDecodeError:
            continue
    # Last resort (fallback to default)
    return pd.read_csv(path)


def load_all_region_data(clear_existing=True):
    init_db()
    db = SessionLocal()
    files = _load_region_csv_files()
    region_files = [os.path.basename(p) for p in files]
    print(f"Loading {len(files)} files: {', '.join(region_files)}")

    try:
        if clear_existing:
            deleted = db.query(RegionAuctionRecord).delete()
            db.commit()
            print(f"Cleared previous rows: {deleted}개")

        _ensure_region_usage_columns_as_text(db)
        db.commit()

        total_rows = 0
        for path in files:
            df = _read_csv_safe(path)
            missing_cols = [col for col in REQUIRED_COLUMNS if col not in df.columns]
            if missing_cols:
                print(f"[skip] {os.path.basename(path)} - missing columns: {missing_cols}")
                continue

            region_file = os.path.splitext(os.path.basename(path))[0]
            records = []

            for idx, row in df.reset_index().iterrows():
                record = {
                    "region_file": region_file,
                    "row_in_source": int(idx) + 1,
                    "case_number": _to_str(row["사건번호"]),
                    "usage": _to_str(row["용도"]),
                    "ltv_gwangju": _to_str(row["LTV_광주"]),
                    "ltv_jeonbuk": _to_str(row["LTV_전북"]),
                    "province": _to_str(row["시도"]),
                    "district": _to_str(row["시군구"]),
                    "address": _to_str(row["소재지"]),
                    "appraised_value": _to_float(row["감정가"]),
                    "min_price": _to_float(row["최저가"]),
                    "result": _to_str(row["결과"]),
                    "winning_price": _to_float(row["낙찰가"]),
                    "winning_rate": _to_float(row["낙찰율"]),
                    "auction_date": _to_datetime(row["매각일"]),
                    "quarter": _to_str(row["분기"]),
                    "period_type": _to_str(row["기간구분"]),
                }
                records.append(record)

            db.bulk_insert_mappings(RegionAuctionRecord, records)
            db.commit()
            total_rows += len(records)
            print(f"[ok] {region_file}.csv -> {len(records):,} rows")

        print(f"COMPLETED: total rows inserted = {total_rows:,}")

    except Exception as exc:
        db.rollback()
        print(f"ERROR: load failed - {exc}")
        raise
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(
        description="Load all 2-char regional CSV files in data/ into DB table: region_auction_records"
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append to existing rows instead of clear and replace.",
    )
    args = parser.parse_args()

    load_all_region_data(clear_existing=not args.append)


if __name__ == "__main__":
    main()
