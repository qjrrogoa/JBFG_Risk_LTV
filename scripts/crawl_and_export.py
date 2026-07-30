"""자회사 인수인계용 end-to-end 스크립트.

크롤링 -> 전처리 -> DB 적재 -> 시그널 계산 -> raw/signal CSV export까지 한 번에 수행한다.
AI 권고안(advice) 생성 단계는 CSV 결과물에 필요하지 않으므로 제외한다 (OPENAI_API_KEY 불필요).
"""
import argparse
import os
import sys
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)
if os.path.join(PROJECT_ROOT, "backend") not in sys.path:
    sys.path.append(os.path.join(PROJECT_ROOT, "backend"))

from scripts import automate_full_update as pipeline  # noqa: E402
from scripts import export_raw_csv, export_signal_csv  # noqa: E402


def main():
    parser = argparse.ArgumentParser(
        description="크롤링부터 raw/시그널 CSV export까지 한 번에 수행 (AI 권고안 생성 제외)"
    )
    parser.add_argument(
        "--from-yyyymm",
        default=None,
        help="raw 데이터 CSV: 이 연월(YYYYMM)의 매각일부터 export (예: 202607)",
    )
    parser.add_argument(
        "--base-ym",
        default=None,
        help="시그널 CSV: 기준연월(YYYYMM) 지정 (예: 202607). 미지정시 은행별 최신 기준월 사용",
    )
    parser.add_argument("--out-dir", default=export_raw_csv.EXPORT_DIR, help="CSV 저장 폴더")
    parser.add_argument(
        "--no-date-suffix",
        action="store_true",
        help="파일명에 날짜(YYYYMMDD)를 붙이지 않고 고정 파일명으로 덮어쓰기",
    )
    args = parser.parse_args()

    pipeline.log("=== 크롤링 + DB 적재 + 시그널 계산 + CSV export 파이프라인 시작 ===")

    pipeline.run_step("CRAWLING", pipeline.step_crawling)
    pipeline.run_step("PREPROCESSING", pipeline.step_preprocessing)
    pipeline.run_step("FILL_ZERO", pipeline.step_fill_zero)
    pipeline.run_step("LOAD_DB", pipeline.step_load_db)
    pipeline.run_step("SIGNAL_CACHE_BUILD", pipeline.step_build_signal_cache)
    pipeline.run_step("CLEANUP", pipeline.step_cleanup)

    os.makedirs(args.out_dir, exist_ok=True)
    suffix = "" if args.no_date_suffix else f"_{datetime.now().strftime('%Y%m%d')}"

    def step_export():
        raw_path = os.path.join(args.out_dir, f"raw_data{suffix}.csv")
        raw_count = export_raw_csv.export_raw_data(raw_path, from_yyyymm=args.from_yyyymm)
        pipeline.log(f"raw 데이터 export -> {raw_path} ({raw_count:,}건)")

        signal_path = os.path.join(args.out_dir, f"signal_data{suffix}.csv")
        signal_count = export_signal_csv.export_signal_data(signal_path, base_ym=args.base_ym)
        pipeline.log(f"시그널 데이터 export -> {signal_path} ({signal_count:,}건)")

    pipeline.run_step("CSV_EXPORT", step_export)

    pipeline.log("=== 파이프라인 완료 ===")


if __name__ == "__main__":
    main()
