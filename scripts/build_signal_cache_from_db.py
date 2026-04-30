import argparse
import json
import os
import sys
from datetime import datetime
import time

from dateutil.relativedelta import relativedelta
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.join(PROJECT_ROOT, "backend"))

from database import (
    init_db,
    RegionAuctionRecord,
    SessionLocal,
    SignalCache,
    ensure_signal_cache_columns,
)
import services


def _month_end(dt):
    period = pd.Timestamp(dt).to_period("M")
    return (period.to_timestamp() + pd.offsets.MonthEnd(0)).to_pydatetime()


def _to_ym_dt(raw: str) -> datetime:
    if not raw:
        raise ValueError("yyyy or yyyymm 값이 필요합니다.")

    parsed = None
    for fmt in ("%Y%m", "%Y-%m", "%Y%m%d", "%Y-%m-%d"):
        try:
            parsed = pd.to_datetime(raw, format=fmt)
            break
        except (ValueError, TypeError):
            continue

    if parsed is None:
        parsed = pd.to_datetime(raw)
    return _month_end(parsed)


def _float_or_none(v):
    if v is None:
        return None
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return None
    if pd.isna(fv):
        return None
    return fv


def _int_or_zero(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _metric_maps_from_matrix_row(row: dict):
    met = row.get("met") if isinstance(row, dict) else None
    avg_src = met.get("avg", {}) if isinstance(met, dict) else {}
    cnt_src = met.get("count", {}) if isinstance(met, dict) else {}

    def avg_value(month_key: str, label: str):
        if isinstance(avg_src, dict):
            value = avg_src.get(month_key)
            if value is None:
                value = avg_src.get(int(month_key))
            numeric = _float_or_none(value)
            if numeric is not None:
                return numeric
        return _float_or_none(row.get(label))

    def cnt_value(month_key: str, label: str):
        if isinstance(cnt_src, dict):
            value = cnt_src.get(month_key)
            if value is None:
                value = cnt_src.get(int(month_key))
            parsed = _int_or_zero(value)
            if parsed:
                return parsed
        return _int_or_zero(row.get(f"{label}_count"))

    avg_map = {
        "3": avg_value("3", "3개월"),
        "6": avg_value("6", "6개월"),
        "12": avg_value("12", "12개월"),
        "36": avg_value("36", "3년"),
        "60": avg_value("60", "5년"),
    }
    cnt_map = {
        "3": cnt_value("3", "3개월"),
        "6": cnt_value("6", "6개월"),
        "12": cnt_value("12", "12개월"),
        "36": cnt_value("36", "3년"),
        "60": cnt_value("60", "5년"),
    }
    return avg_map, cnt_map


def _upsert_signal_cache_rows(db_factory, payloads: list[dict], retry_count: int = 3, chunk_size: int = 400):
    if not payloads:
        return 0

    total_rows = 0
    now = datetime.now()

    for chunk_start in range(0, len(payloads), chunk_size):
        chunk = payloads[chunk_start : chunk_start + chunk_size]
        for record in chunk:
            if "created_at" not in record:
                record["created_at"] = now

        for record in chunk:
            record["updated_at"] = now

        stmt = insert(SignalCache).values(chunk)
        excluded = stmt.excluded
        base_update_keys = [k for k in chunk[0].keys() if k != "created_at"]
        update_map = {k: getattr(excluded, k) for k in base_update_keys}
        upsert_stmt = stmt.on_conflict_do_update(
            index_elements=[
                "bank_name",
                "base_ym",
                "region",
                "category",
                "usage_type",
                "ltv_value",
            ],
            set_=update_map,
        )

        retry_success = False
        for attempt in range(1, retry_count + 1):
            db = db_factory()
            try:
                db.execute(upsert_stmt)
                db.commit()
                total_rows += len(chunk)
                retry_success = True
                break
            except (OperationalError, SQLAlchemyError) as e:
                db.rollback()
                db.close()
                if attempt >= retry_count:
                    raise e
                wait_sec = min(2, attempt)
                print(f"    [db-retry] signal_cache upsert chunk failed, retrying in {wait_sec}s ({attempt}/{retry_count}) ...")
                time.sleep(wait_sec)
            finally:
                db.close()

        if not retry_success:
            raise RuntimeError(f"upsert chunk failed after {retry_count} retries: key={chunk[0]}")

    return total_rows


def _month_bounds_from_db(db: Session) -> tuple[datetime | None, datetime | None]:
    min_dt, max_dt = db.query(
        func.min(RegionAuctionRecord.auction_date),
        func.max(RegionAuctionRecord.auction_date),
    ).first()

    if min_dt is None or max_dt is None:
        return None, None
    return pd.to_datetime(min_dt), pd.to_datetime(max_dt)


def _build_month_ends(min_dt: datetime, max_dt: datetime) -> list[datetime]:
    start = pd.Timestamp(min_dt).to_period("M").to_timestamp().to_pydatetime()
    end = pd.Timestamp(max_dt).to_period("M").to_timestamp().to_pydatetime()

    months = []
    cur = start
    while cur <= end:
        months.append(_month_end(cur))
        cur = (pd.Timestamp(cur) + pd.DateOffset(months=1)).to_pydatetime()
    return months


def _coerce_months(start_ym: str | None, end_ym: str | None, db: Session):
    min_dt, max_dt = _month_bounds_from_db(db)
    if min_dt is None or max_dt is None:
        return []

    all_months = _build_month_ends(min_dt.to_pydatetime(), max_dt.to_pydatetime())

    if not start_ym and not end_ym:
        return all_months

    if start_ym:
        start_dt = _to_ym_dt(start_ym)
    else:
        start_dt = all_months[0]

    if end_ym:
        end_dt = _to_ym_dt(end_ym)
    else:
        end_dt = all_months[-1]

    return [m for m in all_months if start_dt <= m <= end_dt]


def build_signal_cache(
    banks: list[str],
    months: list[datetime],
    outlier_thresh: float = 0.3,
    min_cnt: int = 1,
    append_mode: bool = False,
) -> None:
    init_db()
    ensure_signal_cache_columns()
    db_meta = SessionLocal()

    try:
        if not append_mode and months:
            target_yms = [m.strftime("%Y%m") for m in months]
            for b in banks:
                deleted = (
                    db_meta.query(SignalCache)
                    .filter(SignalCache.bank_name == b, SignalCache.base_ym.in_(target_yms))
                    .delete(synchronize_session=False)
                )
                if deleted:
                    print(f"[clear] {b} {target_yms[0]}~{target_yms[-1]} => {deleted} rows")
            db_meta.commit()

        total_rows = 0

        for bank in banks:
            bank_rows = 0
            failed_months: list[str] = []

            for month_dt in months:
                month_upsert = 0
                ym = month_dt.strftime("%Y%m")
                base_date = month_dt.strftime("%Y-%m-%d")

                try:
                    matrix_df, raw_urgent_list = services.get_aggregated_data(
                        bank,
                        base_date,
                        outlier_thresh=outlier_thresh,
                        min_cnt=min_cnt,
                    )
                except Exception as e:
                    failed_months.append(ym)
                    print(f"    [warn] {bank} {ym} failed in aggregation: {e}")
                    continue

                if matrix_df.empty:
                    continue

                payloads = []
                raw_signal_lookup = {}
                for item in raw_urgent_list:
                    signal = item.get("signal") or {}
                    row_key = (
                        item.get("reg"),
                        item.get("category"),
                        item.get("usage_type"),
                        round(_float_or_none(item.get("ltv_val")) or 80.0, 4),
                    )
                    raw_signal_lookup[row_key] = signal

                for row in matrix_df.to_dict(orient="records"):
                    region = row.get("지역")
                    category = row.get("대분류")
                    usage_type = row.get("용도")
                    ltv_value = _float_or_none(row.get("LTV")) or 80.0
                    key = (region, category, usage_type, round(ltv_value, 4))
                    signal = raw_signal_lookup.get(key, {})

                    avg_map, cnt_map = _metric_maps_from_matrix_row(row)
                    met = {
                        "avg": avg_map,
                        "count": cnt_map,
                    }

                    payload = {
                        "bank_name": bank,
                        "base_ym": ym,
                        "region": region,
                        "category": category,
                        "usage_type": usage_type,
                        "ltv_value": ltv_value,
                        "signal_tone": signal.get("tone"),
                        "signal_direction": signal.get("direction"),
                        "suggested_ltv": _float_or_none(signal.get("suggested_ltv")),
                        "adjust_delta": _float_or_none(signal.get("adjust_delta")),
                        "gap3": _float_or_none(signal.get("gap3")),
                        "avg_3": _float_or_none(avg_map.get("3")),
                        "avg_6": _float_or_none(avg_map.get("6")),
                        "avg_12": _float_or_none(avg_map.get("12")),
                        "avg_36": _float_or_none(avg_map.get("36")),
                        "avg_60": _float_or_none(avg_map.get("60")),
                        "cnt_3": _int_or_zero(cnt_map.get("3")),
                        "cnt_6": _int_or_zero(cnt_map.get("6")),
                        "cnt_12": _int_or_zero(cnt_map.get("12")),
                        "cnt_36": _int_or_zero(cnt_map.get("36")),
                        "cnt_60": _int_or_zero(cnt_map.get("60")),
                        "metric_blob": json.dumps(met, ensure_ascii=False),
                        "reason": signal.get("reason"),
                        "is_modified": False,
                    }
                    payload["created_at"] = datetime.now()
                    payloads.append(payload)
                    total_rows += 1
                    bank_rows += 1
                    month_upsert += 1

                upserted = _upsert_signal_cache_rows(
                    SessionLocal, payloads, chunk_size=400
                )
                if upserted != len(payloads):
                    print(f"    [warn] upsert row mismatch: expected {len(payloads)}, done {upserted}")
                print(f"    [build] {bank} {ym} {month_upsert} rows upserted")

            print(f"[{bank}] month_count={len(months)} matrix_rows={bank_rows}")
            if failed_months:
                print(f"[{bank}] failed months={','.join(failed_months)}")

        print(f"COMPLETED. inserted_or_updated_rows={total_rows}")

    finally:
        db_meta.close()


def main():
    parser = argparse.ArgumentParser(
        description="Build signal cache table using DB auction data and current signal logic (no AI)"
    )
    parser.add_argument("--bank", action="append", choices=list(services.BANK_CONFIG.keys()), help="Target bank. 기본값은 모든 은행")
    parser.add_argument("--base-ym", help="단일 기준월 (예: 202604, 2026-06)")
    parser.add_argument("--start-ym", help="시작 기준월 (예: 202601)")
    parser.add_argument("--end-ym", help="종료 기준월 (예: 202604)")
    parser.add_argument("--append", action="store_true", help="기존 시그널 행을 제거하지 않고 추가/갱신합니다.")
    parser.add_argument("--outlier-thresh", type=float, default=0.3)
    parser.add_argument("--min-cnt", type=int, default=1)
    args = parser.parse_args()

    target_banks = args.bank or list(services.BANK_CONFIG.keys())

    init_db()
    db = SessionLocal()
    try:
        if args.base_ym:
            months = [_to_ym_dt(args.base_ym)]
        else:
            months = _coerce_months(args.start_ym, args.end_ym, db)
    finally:
        db.close()

    if not months:
        print("No auction month found in DB. nothing to build.")
        return

    if args.start_ym and args.end_ym and _to_ym_dt(args.start_ym) > _to_ym_dt(args.end_ym):
        raise SystemExit("--start-ym should be earlier than --end-ym")

    build_signal_cache(
        banks=target_banks,
        months=months,
        outlier_thresh=args.outlier_thresh,
        min_cnt=args.min_cnt,
        append_mode=args.append,
    )


if __name__ == "__main__":
    main()
