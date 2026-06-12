import argparse
import glob
import os
import sys
from datetime import datetime

import pandas as pd
from sqlalchemy import inspect as sa_inspect, text
from sqlalchemy.dialects.postgresql import insert as pg_insert


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
BACKEND_DIR = os.path.join(PROJECT_ROOT, "backend")

if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)
if BACKEND_DIR not in sys.path:
    sys.path.append(BACKEND_DIR)

from database import RegionAuctionRecord, SessionLocal, init_db


DATA_DIR = os.path.join(PROJECT_ROOT, "data")

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
            pass


def _load_region_csv_files(file_pattern: str = "*.csv"):
    files = []
    for path in sorted(glob.glob(os.path.join(DATA_DIR, file_pattern))):
        filename = os.path.basename(path)
        if filename in EXCLUDE_FILES:
            continue
        files.append(path)
    return files


def _read_csv_safe(path):
    for enc in ["utf-8-sig", "cp949"]:
        try:
            return pd.read_csv(path, encoding=enc)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(path)


def _build_records(df: pd.DataFrame, region_file: str):
    records = []
    for idx, row in df.reset_index(drop=True).iterrows():
        case_number = _to_str(row["사건번호"])
        if not case_number:
            continue

        records.append(
            {
                "region_file": region_file,
                "row_in_source": int(idx) + 1,
                "case_number": case_number,
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
                "created_at": datetime.now(),
            }
        )
    return records


def _upsert_records(db, records):
    if not records:
        return 0

    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        stmt = pg_insert(RegionAuctionRecord).values(records)
        update_cols = {
            key: getattr(stmt.excluded, key)
            for key in records[0].keys()
            if key not in {"case_number", "created_at"}
        }
        upsert_stmt = stmt.on_conflict_do_update(
            constraint="uq_auction_case_number",
            set_=update_cols,
        )
        result = db.execute(upsert_stmt)
        return result.rowcount or len(records)

    affected = 0
    for record in records:
        existing = (
            db.query(RegionAuctionRecord)
            .filter(RegionAuctionRecord.case_number == record["case_number"])
            .one_or_none()
        )
        if existing is None:
            db.add(RegionAuctionRecord(**record))
        else:
            for key, value in record.items():
                if key not in {"case_number", "created_at"}:
                    setattr(existing, key, value)
        affected += 1
    return affected


def load_all_region_data(clear_existing: bool = True, file_pattern: str = "*.csv"):
    init_db()
    db = SessionLocal()
    files = _load_region_csv_files(file_pattern=file_pattern)
    file_names = [os.path.basename(path) for path in files]
    print(f"Loading {len(files)} files: {', '.join(file_names)}")

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
            records = _build_records(df, region_file)
            affected = _upsert_records(db, records)
            db.commit()

            total_rows += affected
            print(f"[ok] {region_file}.csv -> {affected:,} rows upserted")

        print(f"COMPLETED: total rows inserted_or_updated = {total_rows:,}")

    except Exception as exc:
        db.rollback()
        print(f"ERROR: load failed - {exc}")
        raise
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(
        description="Load regional CSV files in data/ into DB table: region_auction_records"
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append or upsert into existing rows instead of clear and replace.",
    )
    parser.add_argument(
        "--file-pattern",
        default="*.csv",
        help="Glob pattern under data/ to load. Example: *_new.csv",
    )
    args = parser.parse_args()

    load_all_region_data(
        clear_existing=not args.append,
        file_pattern=args.file_pattern,
    )


if __name__ == "__main__":
    main()
