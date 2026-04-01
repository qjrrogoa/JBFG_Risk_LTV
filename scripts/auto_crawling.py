import os
import argparse
from full_crawling import main as crawl_main, CrawlConfig

def main():
    # 1. 인자값 파싱
    parser = argparse.ArgumentParser(description="인포케어 법원경매 데이터 자동 크롤러")
    parser.add_argument("--region", type=str, help="크롤링할 지역명을 입력하세요 (예: 서울, 부산, 경기, 전남 등)")
    args = parser.parse_args()

    region = args.region

    # 만약 인자로 들어오지 않았다면 사용자 입력 받기
    if not region:
        print("=" * 50)
        print("인포케어 법원경매 데이터 자동 크롤러")
        print("=" * 50)
        region = input("크롤링할 지역명을 입력하세요 (예: 서울, 부산, 경기, 전남 등): ").strip()
    
    if not region:
        print("Error: 지역명이 입력되지 않았습니다.")
        return

    # 2. 설정 구성
    cfg = CrawlConfig()
    cfg.region = region
    
    # 데이터 저장 폴더 확인 및 생성
    if not os.path.exists("data"):
        os.makedirs("data")
        print("[INFO] 'data' 디렉토리를 생성했습니다.")

    # 파일명 설정 (지역명.csv)
    cfg.output_csv = f"data/{region}.csv"
    
    print(f"\n[START] {region} 지역 크롤링을 시작합니다.")
    print(f"[SAVE] 결과 파일: {cfg.output_csv}")
    print("-" * 50)

    # 3. 크롤링 실행
    try:
        crawl_main(cfg)
        print("\n" + "=" * 50)
        print(f"[SUCCESS] {region} 지역 크롤링이 완료되었습니다.")
        print(f"[PATH] {os.path.abspath(cfg.output_csv)}")
        print("=" * 50)
    except Exception as e:
        print(f"\n[ERROR] 크롤링 중 오류가 발생했습니다: {e}")

if __name__ == "__main__":
    main()
