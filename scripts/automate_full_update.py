
import os
import sys
import subprocess
import time
from datetime import datetime

# 프로젝트 루트 및 백엔드 경로 설정
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(PROJECT_ROOT)
sys.path.append(os.path.join(PROJECT_ROOT, "backend"))

import llm_advisor
from backend import services
from scripts import full_crawling, load_region_csvs_to_db

# REGIONS = ["서울", "인천", "경기", "부산", "대구", "대전", "전남광주", "울산", "전북", "경북", "경남", "제주", "충남", "충북", "강원", "세종"]
REGIONS = ["전남광주", "대구"]

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

def run_step(name, func):
    log(f">>> STEP START: {name}")
    start_time = time.time()
    try:
        func()
        elapsed = time.time() - start_time
        log(f"<<< STEP COMPLETED: {name} ({elapsed:.1f}s)")
    except Exception as e:
        log(f"!!! STEP FAILED: {name} - {e}")
        raise

def step_crawling():
    """1. 브라우저 1회 실행 → 전 지역 순회 크롤링"""
    from dateutil.relativedelta import relativedelta
    import shutil

    # start_date = datetime.now() - relativedelta(months=1)
    start_date = datetime(2026, 5, 1)

    s_year = start_date.year
    s_month = start_date.month
    s_day = 1

    # 이미 수집 완료된 지역 건너뛰기 목록 생성
    regions_to_crawl = []
    for r in REGIONS:
        new_csv = f"data/{r}_new.csv"
        if os.path.exists(new_csv) and os.path.getsize(new_csv) > 500:
            log(f"Skipping region: {r} (Already crawled)")
            continue
        regions_to_crawl.append(r)

    if not regions_to_crawl:
        log("All regions already crawled. Nothing to do.")
        return

    log(f"Regions to crawl: {regions_to_crawl}")

    # 날짜 범위 생성 (모든 지역 동일)
    ranges = full_crawling.generate_half_year_ranges_upto_today(
        start_year=s_year,
        end_year=datetime.now().year,
        start_month=s_month,
        start_day=s_day,
    )
    log(f"Date ranges: {ranges}")

    # 브라우저 1회만 실행
    cfg = full_crawling.CrawlConfig()
    driver = None
    profile_dir = None

    try:
        driver, wait, profile_dir = full_crawling.build_driver(cfg)
        full_crawling.login_and_go_to_total_search(driver, wait, cfg)
        log("✅ Browser ready. Starting multi-region crawl...")

        for r in regions_to_crawl:
            new_csv = f"data/{r}_new.csv"
            log(f"--- Region: {r} ---")

            try:
                for start, end in ranges:
                    log(f"  Searching: {start} ~ {end}")
                    full_crawling.set_search_filters_and_search(driver, wait, cfg, r, start, end)
                    rows = full_crawling.crawl_current_result_pages(driver, wait, cfg)
                    log(f"  Collected: {len(rows)} rows")

                    if rows:
                        full_crawling.append_rows_to_csv(new_csv, rows)

                    full_crawling.jitter_sleep(cfg)

                log(f"✅ Region {r} completed.")
            except Exception as e:
                log(f"❌ Error in region {r}: {e} (Skipping)")
                # 검색 페이지로 복귀 시도
                try:
                    full_crawling.ensure_info_main(driver, wait)
                except Exception:
                    pass
                time.sleep(3)
                continue

    except Exception as e:
        log(f"❗ Fatal crawling error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if driver is not None:
            try:
                full_crawling.clear_site_session(driver)
            except Exception:
                pass
            try:
                driver.quit()
                log("Driver closed.")
            except Exception:
                pass
        if profile_dir:
            try:
                shutil.rmtree(profile_dir, ignore_errors=True)
            except Exception:
                pass

def step_preprocessing():
    """2. 수집된 신규 데이터 전처리 (단순 데이터 보정 및 용도 매핑)"""
    for r in REGIONS:
        new_csv = f"data/{r}_new.csv"
        if os.path.exists(new_csv):
            from scripts import batch_ltv_update
            batch_ltv_update.process_file(new_csv)

def step_fill_zero():
    """2.5. 신규 수집 데이터 중 빈 낙찰가 보정"""
    from scripts import fill_zero
    csv_paths = [f"data/{r}_new.csv" for r in REGIONS if os.path.exists(f"data/{r}_new.csv")]
    if csv_paths:
        log("Starting bulk fill_zero for all regions...")
        fill_zero.fill_zero_multiple_csvs(csv_paths)

def step_load_db():
    """3. DB 업서트 (신규 데이터만 반영)"""
    load_region_csvs_to_db.load_all_region_data(clear_existing=False, file_pattern="*_new.csv")

def step_cleanup():
    """4. 사용한 임시 파일 정리 및 마스터 백업"""
    for r in REGIONS:
        new_csv = f"data/{r}_new.csv"
        if os.path.exists(new_csv):
            os.remove(new_csv)

def step_build_signal_cache():
    """4. 시그널 캐시 구축 (통계 분석 및 시그널 판별)"""
    banks = ["광주은행", "전북은행"]
    for bank in banks:
        log(f"Building signal cache for {bank}...")
        services.get_signal_cache_rows(bank)

def step_ai_analysis():
    """5. AI 권고안 일괄 생성 (Red/Yellow 시그널 대상)"""
    banks = ["광주은행", "전북은행"]
    for bank in banks:
        log(f"Starting AI Analysis for {bank}...")
        rows, _ = services.get_signal_cache_rows(bank)
        services.fetch_all_advice(rows, bank, async_only=False)

def main():
    log("=== LTV 리스크 관리 시스템 데이터 업데이트 자동화 시작 ===")
    
    try:
        # 1. 크롤링
        run_step("CRAWLING", step_crawling)
        
        # 2. 전처리
        run_step("PREPROCESSING", step_preprocessing)

        # 2.5. 빈 낙찰가 보정
        run_step("FILL_ZERO", step_fill_zero)
        
        # 3. DB 적재
        run_step("LOAD_DB", step_load_db)
        
        # 4. 시그널 캐시 구축
        run_step("SIGNAL_CACHE_BUILD", step_build_signal_cache)
        
        # 5. AI 분석 수행
        run_step("AI_ADVICE_GENERATION", step_ai_analysis)
        
        # 6. 임시 파일 정리
        run_step("CLEANUP", step_cleanup)
        
        log("=== 모든 업데이트 공정이 성공적으로 완료되었습니다! ===")
        
    except Exception as e:
        log(f"업데이트 중단됨: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
