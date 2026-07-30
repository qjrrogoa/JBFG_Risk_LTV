"""raw 데이터 CSV + 시그널 CSV를 한 번에 뽑는 편의 스크립트.
개별 export가 필요하면 export_raw_csv.py / export_signal_csv.py를 직접 실행하면 됨.
"""
import argparse
import os
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))

if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

from export_raw_csv import EXPORT_DIR, export_raw_data  # noqa: E402
from export_signal_csv import export_signal_data  # noqa: E402


def main():
    parser = argparse.ArgumentParser(
        description="자회사 전달용 raw 데이터 / 시그널 계산 결과 CSV export (두 파일 동시 생성)"
    )
    parser.add_argument(
        "--from-yyyymm",
        default=None,
        help="raw 데이터: 이 연월(YYYYMM)의 매각일부터 export (예: 202607)",
    )
    parser.add_argument(
        "--base-ym",
        default=None,
        help="시그널 데이터: 기준연월(YYYYMM) 지정 (예: 202607). 미지정시 은행별 최신 기준월 사용",
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
    raw_count = export_raw_data(raw_path, from_yyyymm=args.from_yyyymm)
    print(f"[OK] raw 데이터 export -> {raw_path} ({raw_count:,}건)")

    signal_path = os.path.join(args.out_dir, f"signal_data{suffix}.csv")
    signal_count = export_signal_data(signal_path, base_ym=args.base_ym)
    print(f"[OK] 시그널 데이터 export -> {signal_path} ({signal_count:,}건)")


if __name__ == "__main__":
    main()
