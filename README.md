# LTV 적정성 검증 Agent

법원경매 낙찰가율 데이터를 기준으로 지역·용도별 LTV 적정성을 점검하는 운영용 시스템입니다.

## 운영 환경

- Backend: `Render`
- Database: `Supabase Postgres`
- Frontend: `Vite + React`
- 운영 주기: `월 1회 배치`

핵심 흐름:

```text
크롤링 -> CSV 전처리 -> DB 적재 -> signal_cache 생성 -> 대시보드 조회
```

## 주요 기능

- 조정 대상 / 검토 대상 조회
- 지역·용도별 적정성 매트릭스 조회
- 상세 통계 / 차트 / AI 권고안 조회
- LTV 저장 / 되돌리기
- 변경 이력 확인

## 환경 변수

```bash
cp .env.example .env
cp frontend/.env.example frontend/.env.development
```

필수:

| 변수명 | 설명 |
| --- | --- |
| `DATABASE_URL` | Supabase Postgres 연결 문자열 |
| `INFOCARE_USER_ID` | 크롤링 계정 |
| `INFOCARE_PASSWORD` | 크롤링 비밀번호 |

선택:

| 변수명 | 설명 |
| --- | --- |
| `OPENAI_API_KEY` | AI 권고안 / 챗봇 사용 시 필요 |
| `LTV_ADVISOR_MODEL` | AI 모델 지정 |
| `LTV_USE_WEB_SEARCH` | AI 권고안 생성 시 웹 검색 사용 여부 |

프론트 배포 시:

```dotenv
VITE_API_BASE_URL=https://your-render-service.onrender.com
```

주의:

- DB 연결은 Supabase API 키가 아니라 `DATABASE_URL`로 합니다.
- `anon key`, `service_role key`, 프로젝트 ID만으로는 연결되지 않습니다.

## 로컬 실행

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd frontend && npm install && cd ..
```

백엔드:

```bash
uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

프론트:

```bash
cd frontend
npm run dev:local
```

헬스체크:

```bash
curl http://127.0.0.1:8000/api/health
```

## 새 DB 초기 적재

새 Supabase DB에 CSV 기준으로 처음 적재할 때:

```bash
python3 backend/migrate_to_db.py
python3 scripts/load_region_csvs_to_db.py
python3 scripts/build_signal_cache_from_db.py
```

역할:

- `backend/migrate_to_db.py`: `ltv_standards` 적재
- `scripts/load_region_csvs_to_db.py`: `region_auction_records` 적재
- `scripts/build_signal_cache_from_db.py`: `signal_cache` 생성

참고:

- `migrate_to_db.py`는 기존 DB 복사 스크립트가 아닙니다.
- 현재 `DATABASE_URL` 대상 DB에 로컬 CSV를 기준으로 적재합니다.

## 월 1회 운영

```bash
python3 scripts/automate_full_update.py
```

배치 내용:

1. 전 지역 크롤링
2. 신규 CSV 전처리
3. 빈 낙찰가 보정
4. DB 업서트
5. `signal_cache` 재생성
6. AI 권고안 생성
7. 임시 `_new.csv` 정리

실행 전 확인:

- `DATABASE_URL`
- `INFOCARE_USER_ID`, `INFOCARE_PASSWORD`
- Chrome 실행 가능 여부

실행 후 확인:

- `/api/health` 정상 응답
- 대시보드 로딩 정상 여부
- 기준월 변경 시 데이터 조회 정상 여부

## 사용자 DB로 전환

사용자 Supabase DB로 넘길 때:

1. 사용자 측 `DATABASE_URL` 확보
2. `.env`와 Render의 `DATABASE_URL` 교체
3. 아래 명령 실행

```bash
python3 backend/migrate_to_db.py
python3 scripts/load_region_csvs_to_db.py
python3 scripts/build_signal_cache_from_db.py
```

이 방식으로 재구성되는 데이터:

- `ltv_standards`
- `region_auction_records`
- `signal_cache`

자동 이전되지 않는 데이터:

- `users`
- `ltv_logs`

## 자주 쓰는 명령어

| 목적 | 명령어 |
| --- | --- |
| 새 DB 초기 적재 | `python3 backend/migrate_to_db.py` |
| 지역 CSV 전체 적재 | `python3 scripts/load_region_csvs_to_db.py` |
| signal cache 생성 | `python3 scripts/build_signal_cache_from_db.py` |
| 월간 전체 배치 | `python3 scripts/automate_full_update.py` |
| 단일 지역 크롤링 | `python3 scripts/auto_crawling.py --region 서울` |

## 장애 확인

`DATABASE_URL not found in .env file`

- `.env` 누락 또는 `DATABASE_URL` 비어 있음

DB 연결 실패

- Supabase API 키가 아니라 Postgres 연결 문자열을 넣었는지 확인

크롤링 실패

- Chrome 실행 가능 여부 확인
- 크롤링 계정 정보 확인

AI 기능 미동작

- `OPENAI_API_KEY` 확인
- AI 없이도 초기 적재와 월간 배치는 진행 가능
