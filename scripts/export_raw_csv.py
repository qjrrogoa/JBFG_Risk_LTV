from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
BACKEND_DIR = os.path.join(PROJECT_ROOT, "backend")

if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)
if BACKEND_DIR not in sys.path:
    sys.path.append(BACKEND_DIR)

from database import RegionAuctionRecord, SessionLocal  # noqa: E402


EXPORT_DIR = os.path.join(PROJECT_ROOT, "data", "export")

# 원본 크롤링 원시 컬럼만 (LTV_광주 / LTV_전북 매핑 컬럼은 제외)
RAW_COLUMNS = {
    "case_number": "사건번호",
    "usage": "용도",
    "province": "시도",
    "district": "시군구",
    "address": "소재지",
    "appraised_value": "감정가",
    "min_price": "최저가",
    "result": "결과",
    "winning_price": "낙찰가",
    "winning_rate": "낙찰율",
    "auction_date": "매각일",
    "quarter": "분기",
    "period_type": "기간구분",
}


def _parse_yyyymm(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y%m")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"'{value}'는 YYYYMM 형식이 아닙니다 (예: 202607)"
        ) from exc


def export_raw_data(output_path: str, from_yyyymm: str | None = None) -> int:
    db = SessionLocal()
    try:
        query = db.query(RegionAuctionRecord)
        if from_yyyymm:
            cutoff = _parse_yyyymm(from_yyyymm)
            query = query.filter(RegionAuctionRecord.auction_date >= cutoff)
        df = pd.read_sql(query.statement, db.get_bind())
    finally:
        db.close()

    df = df[list(RAW_COLUMNS.keys())].rename(columns=RAW_COLUMNS)
    df = df.sort_values(["매각일", "사건번호"])
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    return len(df)


def main():
    parser = argparse.ArgumentParser(description="raw 경매 데이터 CSV export")
    parser.add_argument(
        "--from-yyyymm",
        default=None,
        help="이 연월(YYYYMM)의 매각일부터 export (예: 202607). 미지정시 전체 기간",
    )
    parser.add_argument("--out-dir", default=EXPORT_DIR, help="CSV 저장 폴더")
    parser.add_argument(
        "--no-date-suffix",
        action="store_true",
        help="파일명에 날짜(YYYYMMDD)를 붙이지 않고 고정 파일명으로 덮어쓰기",
    )
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    suffix = "" if args.no_date_suffix else f"_{datetime.now().strftime('%Y%m%d')}"
    raw_path = os.path.join(args.out_dir, f"raw_data{suffix}.csv")

    count = export_raw_data(raw_path, from_yyyymm=args.from_yyyymm)
    range_note = f" ({args.from_yyyymm} 이후)" if args.from_yyyymm else ""
    print(f"[OK] raw 데이터 export{range_note} -> {raw_path} ({count:,}건)")


if __name__ == "__main__":
    main()
