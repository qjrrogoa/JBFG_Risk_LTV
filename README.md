# LTV 적정성 검증 Agent

법원경매 낙찰가율 데이터를 기반으로 지역·용도별 LTV 적정성을 점검하고, 조정 우선순위와 AI 권고안을 함께 보여주는 내부 운영용 대시보드입니다.  
현재 `광주은행`, `전북은행` 두 은행 기준을 지원하며, 월 1회 데이터 수집과 배치 갱신을 기준으로 운영하도록 구성되어 있습니다.

## 1. 이 프로젝트가 하는 일

이 시스템은 아래 흐름으로 동작합니다.

```text
Infocare 법원경매 크롤링
-> 지역별 CSV 전처리
-> PostgreSQL 적재
-> 기간별 낙찰가율 집계 / signal_cache 생성
-> AI 권고안 생성
-> FastAPI API 제공
-> React 대시보드에서 조회 / 조정 / 로그 확인
```

핵심 목적은 "현재 은행 LTV가 실제 최근 낙찰가율 흐름 대비 과도한지, 보수적인지"를 빠르게 판단하는 것입니다.

## 2. 주요 기능

| 구분 | 설명 |
| --- | --- |
| 로그인 / 회원가입 | 은행별 사용자 계정을 생성하고 로그인합니다. |
| 요약 대시보드 | 월 기준 조정 대상 `red`, 검토 대상 `yellow` 건수를 카드 형태로 보여줍니다. |
| 긴급 우선순위 테이블 | 조정이 필요한 지역·용도를 정렬해서 보여주고, AI 권고안과 최종 LTV 적용값을 바로 입력할 수 있습니다. |
| 적정성 매트릭스 | 지역 / 대분류 / 용도 / 상태 필터 기준으로 3개월, 6개월, 12개월, 3년, 5년 상태를 한 번에 비교합니다. |
| 상세 모달 | 이동평균 추이, 최근 12개월 시계열, 기간별 통계, AI 권고 사유를 확인합니다. |
| LTV 기준표 조회 | 선택한 기준일 시점에 실제 적용되던 LTV 기준표를 테이블로 조회합니다. |
| LTV 변경 / 되돌리기 | 특정 지역·용도의 LTV를 즉시 저장하거나 이전 기준으로 롤백할 수 있습니다. |
| 변경 이력 로그 | 최근 LTV 변경 내역을 DB 로그 기준으로 확인합니다. |
| AI 챗봇 | 자연어로 조정 대상, 지역 상세, 통계 비교, 시장 뉴스 요약을 조회할 수 있습니다. |

## 3. 디렉터리 구조

| 경로 | 역할 |
| --- | --- |
| `backend/main.py` | FastAPI 진입점 |
| `backend/services.py` | 집계, 시그널 계산, LTV 저장/롤백, 로그 처리 |
| `backend/database.py` | SQLAlchemy 모델 및 DB 연결 |
| `backend/chat_agent.py` | LangChain 기반 챗봇 |
| `llm_advisor.py` | AI 권고안 생성 로직 |
| `scripts/automate_full_update.py` | 월간 전체 배치 실행 스크립트 |
| `scripts/full_crawling.py` | Selenium 전체 크롤러 |
| `scripts/auto_crawling.py` | 단일 지역 수동 크롤링 진입점 |
| `scripts/load_region_csvs_to_db.py` | 지역 CSV를 DB에 업서트 |
| `scripts/build_signal_cache_from_db.py` | `signal_cache` 재생성 |
| `backend/migrate_to_db.py` | 은행별 LTV 기준 CSV를 DB로 적재 |
| `frontend/src/App_v2.jsx` | 메인 대시보드 화면 |
| `data/` | 지역 CSV, 기준표 CSV, 매핑 CSV, 로그성 데이터 |

## 4. 기술 스택

- Backend: FastAPI, SQLAlchemy, PostgreSQL
- Frontend: React, Vite, Tailwind CSS
- Batch / Data: pandas, numpy
- Crawling: Selenium + Google Chrome
- AI: OpenAI Responses API, LangChain, LangGraph

## 5. 사전 준비

필수 준비물입니다.

- Python `3.10+`
- Node.js LTS
- PostgreSQL
- Google Chrome
- `pip`, `npm`

참고:

- Selenium 4를 사용하므로 환경에 따라 ChromeDriver를 자동으로 잡습니다. (인터넷 망에서만 크롤링 가능)

## 6. 환경 변수 설정

### 6-1. 루트 `.env`

루트에서 예시 파일을 복사합니다.

```bash
cp .env.example .env
```

주요 변수는 아래와 같습니다.

| 변수명 | 필수 | 설명 |
| --- | --- | --- |
| `DATABASE_URL` | 예 | PostgreSQL 연결 문자열 |
| `OPENAI_API_KEY` | 권장 | AI 권고안 / 챗봇 / 시장 뉴스 검색용 |
| `INFOCARE_USER_ID` | 예 | Infocare 크롤링 계정 ID |
| `INFOCARE_PASSWORD` | 예 | Infocare 크롤링 계정 비밀번호 |
| `LTV_ADVISOR_MODEL` | 아니오 | AI 권고안 생성 모델, 기본값 `gpt-5-nano` |
| `LTV_USE_WEB_SEARCH` | 아니오 | AI 권고안 생성 시 웹 검색 사용 여부 |
| `LTV_WEB_SEARCH_CONTEXT` | 아니오 | 웹 검색 컨텍스트 크기 |
| `LTV_ADVISOR_MAX_OUTPUT_TOKENS` | 아니오 | AI 권고안 최대 출력 토큰 |
| `LTV_ADVISOR_REASONING_EFFORT` | 아니오 | GPT-5 reasoning effort |
| `LTV_AI_CONCURRENCY` | 아니오 | AI 권고 병렬 처리 수 |

예시:

```dotenv
DATABASE_URL=postgresql://postgres:password@localhost:5432/ltv
OPENAI_API_KEY=sk-...
INFOCARE_USER_ID=your_id
INFOCARE_PASSWORD=your_password
LTV_ADVISOR_MODEL=gpt-5-nano
LTV_USE_WEB_SEARCH=false
LTV_AI_CONCURRENCY=2
```

### 6-2. 프론트엔드 환경 변수

```bash
cp frontend/.env.example frontend/.env.development
```

기본값:

```dotenv
VITE_API_BASE_URL=http://127.0.0.1:8000
```

## 7. 최초 실행 방법

### 7-1. 백엔드 의존성 설치

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 7-2. 프론트엔드 의존성 설치

```bash
cd frontend
npm install
cd ..
```

### 7-3. DB 초기 적재

최초 1회는 아래 순서로 실행하는 것을 권장합니다.

```bash
python backend/migrate_to_db.py
python scripts/load_region_csvs_to_db.py
python scripts/build_signal_cache_from_db.py
```

설명:

- `backend/migrate_to_db.py`
  - `data/LTV_기준(광주은행).csv`, `data/LTV_기준(전북은행).csv`를 `ltv_standards` 테이블에 적재합니다.
- `scripts/load_region_csvs_to_db.py`
  - `data/*.csv` 지역 파일을 `region_auction_records` 테이블에 업서트합니다.
- `scripts/build_signal_cache_from_db.py`
  - 월별 통계와 적정성 판정을 계산해서 `signal_cache`를 만듭니다.

### 7-4. 백엔드 실행

```bash
uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

헬스체크:

```bash
curl http://127.0.0.1:8000/api/health
```

### 7-5. 프론트엔드 실행

```bash
cd frontend
npm run dev:local
```

접속 주소:

- Frontend: `http://127.0.0.1:5173`
- Backend API: `http://127.0.0.1:8000`

## 8. 월 1회 운영 절차

이 프로젝트의 표준 운영 방식은 `매월 1회 배치 실행`입니다.

권장 시점:

- 전월 데이터가 대부분 마감된 뒤
- 보통 `매월 1~5영업일` 사이 실행 권장

### 8-1. 실행 명령

```bash
python scripts/automate_full_update.py
```

### 8-2. 이 스크립트가 내부적으로 하는 일

1. 전월 1일 기준부터 실행일 현재까지를 대상으로 17개 지역 크롤링
2. `_new.csv` 신규 파일 전처리
3. 빈 `낙찰가` 보정
4. `region_auction_records` DB 업서트
5. 은행별 `signal_cache` 재구축
6. `red` / `yellow` 대상 AI 권고안 생성
7. 성공 시 임시 `_new.csv` 정리

운영상 중요한 점:

- 배치가 중간에 실패하면 이미 생성된 `data/*_new.csv`가 남을 수 있습니다.
- 같은 명령을 다시 실행하면, 크롤링 단계에서 이미 생성된 `_new.csv`가 있는 지역은 건너뛰도록 되어 있어 재실행에 비교적 안전합니다.
- 정상 완료되면 `_new.csv`는 정리됩니다.

### 8-3. 월간 운영 체크리스트

배치 실행 전:

- `.env`의 `DATABASE_URL`, `INFOCARE_USER_ID`, `INFOCARE_PASSWORD`, `OPENAI_API_KEY` 확인
- PostgreSQL 접속 가능 여부 확인
- Google Chrome 실행 가능 여부 확인

배치 실행 후:

- 백엔드 헬스체크 정상 여부 확인
- 대시보드에서 기준월 선택 후 `조정 대상 / 검토 대상` 건수 확인
- 상세 모달에서 차트와 AI 권고 문구가 정상 표시되는지 확인
- `admin` 계정으로 로그인해 변경 이력 로그 확인

## 9. 자주 쓰는 운영 명령어

| 목적 | 명령어 |
| --- | --- |
| 월간 전체 배치 | `python scripts/automate_full_update.py` |
| 단일 지역 전체 크롤링 | `python scripts/auto_crawling.py --region 서울` |
| 지역 CSV 전체 DB 적재 | `python scripts/load_region_csvs_to_db.py` |
| 신규 `_new.csv`만 추가 반영 | `python scripts/load_region_csvs_to_db.py --append --file-pattern "*_new.csv"` |
| 특정 월 signal cache 재생성 | `python scripts/build_signal_cache_from_db.py --base-ym 202606` |
| 은행 LTV 기준표 재적재 | `python backend/migrate_to_db.py` |
| 백엔드 실행 | `uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload` |
| 프론트 실행 | `cd frontend && npm run dev:local` |

## 10. 인수인계 포인트

### 10-1. 꼭 알아야 하는 운영 규칙

- 월간 운영의 표준 진입점은 `scripts/automate_full_update.py` 입니다.
- 크롤링 계정 정보는 이제 코드가 아니라 루트 `.env`에서 관리합니다.
- 프론트에서 변경 이력 로그 버튼은 로그인한 `username`이 정확히 `admin`일 때만 보입니다.
- 별도 관리자 권한 테이블은 없으므로, 로그 화면이 필요하면 `admin` 사용자명을 직접 생성해서 사용해야 합니다.
- LTV 변경 이력은 DB의 `ltv_logs` 테이블에 저장됩니다.
- 실제 기준 LTV는 `ltv_standards` 테이블을 기준으로 조회합니다.

### 10-2. 데이터/운영 책임 범위

| 항목 | 관리 포인트 |
| --- | --- |
| 크롤링 계정 | `.env`의 `INFOCARE_USER_ID`, `INFOCARE_PASSWORD` |
| DB 연결 | `.env`의 `DATABASE_URL` |
| AI 기능 | `.env`의 `OPENAI_API_KEY` |
| 기준표 변경 | `data/LTV_기준(광주은행).csv`, `data/LTV_기준(전북은행).csv` 및 `backend/migrate_to_db.py` |
| 용도 매핑 | `data/양행별_용도_리스트_최신.csv` |
| 지역 원천 데이터 | `data/*.csv`, `data/original/*.csv` |

### 10-3. 담당자가 바뀌면 가장 먼저 확인할 것

1. `.env`가 실제 운영값으로 채워져 있는지
2. PostgreSQL에 접근 가능한지
3. `python scripts/automate_full_update.py`가 로컬에서 끝까지 도는지
4. 프론트에서 월 변경, 상세 모달, 저장/되돌리기, 챗봇이 정상 동작하는지

## 11. 장애 대응 가이드

### `DATABASE_URL not found in .env file`

- 루트 `.env`가 없거나 `DATABASE_URL`이 비어 있습니다.
- `.env.example`을 복사해 다시 설정합니다.

### AI 권고안이 비어 있거나 챗봇이 실패함

- `OPENAI_API_KEY` 누락 또는 한도 초과 가능성이 큽니다.
- 권고안은 내부 통계 기반 fallback으로 내려올 수 있지만, 챗봇은 정상 응답하지 못할 수 있습니다.

### 크롤링이 시작되지 않음

- Chrome 실행 가능 여부 확인
- `INFOCARE_USER_ID`, `INFOCARE_PASSWORD` 확인
- 사내망 정책상 Selenium용 드라이버 다운로드가 막히는지 확인

### 일부 지역 데이터만 비정상

- 월간 배치를 다시 실행해 `_new.csv` 스킵/재개 동작을 활용합니다.
- 필요하면 `python scripts/auto_crawling.py --region 지역명`으로 단일 지역을 재수집한 뒤 DB 적재를 다시 수행합니다.

## 12. 운영 메모

- 이 시스템은 "실시간 초단위" 서비스가 아니라 "월 단위 점검용 운영 도구"에 가깝습니다.
- 따라서 배치 성공 여부, 기준표 최신성, 월간 검수 프로세스가 화면 디자인보다 더 중요합니다.
- 인수인계 시에는 코드 설명보다 먼저 `배치 실행 -> 결과 확인 -> 장애 대응` 흐름을 같이 전달하는 것을 권장합니다.
