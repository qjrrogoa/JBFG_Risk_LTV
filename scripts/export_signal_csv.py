from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

import pandas as pd
from sqlalchemy import func

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
BACKEND_DIR = os.path.join(PROJECT_ROOT, "backend")

if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)
if BACKEND_DIR not in sys.path:
    sys.path.append(BACKEND_DIR)

from database import SignalCache, SessionLocal  # noqa: E402


EXPORT_DIR = os.path.join(PROJECT_ROOT, "data", "export")

# AI 권고안(advice_*) 등 내부용 컬럼은 제외, red/yellow(하향 리스크)/green(상향 참고)로 잡힌 시그널만 대상
SIGNAL_COLUMNS = {
    "bank_name": "은행명",
    "base_ym": "기준월",
    "region": "지역",
    "category": "구분",
    "usage_type": "용도",
    "ltv_value": "현재LTV",
    "signal_tone": "시그널",
    "signal_direction": "방향",
    "suggested_ltv": "권고LTV",
    "adjust_delta": "조정폭",
    "gap3": "3개월갭",
    "avg_3": "3개월평균",
    "avg_6": "6개월평균",
    "avg_12": "12개월평균",
    "avg_36": "36개월평균",
    "avg_60": "60개월평균",
    "cnt_3": "3개월건수",
    "cnt_6": "6개월건수",
    "cnt_12": "12개월건수",
    "cnt_36": "36개월건수",
    "cnt_60": "60개월건수",
    "reason": "판단근거",
}


def export_signal_data(
    output_path: str,
    banks=("광주은행", "전북은행"),
    base_ym: str | None = None,
) -> int:
    db = SessionLocal()
    try:
        query = db.query(SignalCache).filter(
            SignalCache.bank_name.in_(banks),
            SignalCache.signal_tone.in_(["red", "yellow", "green"]),
        )

        if base_ym:
            # 기준연월을 명시한 경우 해당 월 스냅샷만 사용
            query = query.filter(SignalCache.base_ym == base_ym)
            target_ym_by_bank = {bank: base_ym for bank in banks}
        else:
            # 미지정시 signal_cache는 매달 이력이 누적되는 테이블이므로
            # 은행별 최신 기준월만 사용
            target_ym_by_bank = dict(
                db.query(SignalCache.bank_name, func.max(SignalCache.base_ym))
                .filter(SignalCache.bank_name.in_(banks))
                .group_by(SignalCache.bank_name)
                .all()
            )

        df = pd.read_sql(query.statement, db.get_bind())
    finally:
        db.close()

    if not base_ym:
        df = df[df.apply(lambda r: r["base_ym"] == target_ym_by_bank.get(r["bank_name"]), axis=1)]

    df = df[list(SIGNAL_COLUMNS.keys())].rename(columns=SIGNAL_COLUMNS)
    df["시그널"] = df["시그널"].str.upper()
    df = df.sort_values(["은행명", "지역", "기준월"])
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    return len(df)


def main():
    parser = argparse.ArgumentParser(description="시그널(RED/YELLOW) 계산 결과 CSV export")
    parser.add_argument(
        "--base-ym",
        default=None,
        help="기준연월(YYYYMM) 지정, 예: 202607 → 해당 월 시그널만 export. 미지정시 은행별 최신 기준월 사용",
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
    signal_path = os.path.join(args.out_dir, f"signal_data{suffix}.csv")

    count = export_signal_data(signal_path, base_ym=args.base_ym)
    ym_note = f" (기준월: {args.base_ym})" if args.base_ym else " (은행별 최신 기준월)"
    print(f"[OK] 시그널 데이터 export{ym_note} -> {signal_path} ({count:,}건)")


if __name__ == "__main__":
    main()
