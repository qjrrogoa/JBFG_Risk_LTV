# LTV 적정성 검증 Agent

법원경매 낙찰가율 데이터를 기준으로 지역·용도별 LTV 적정성을 점검하는 운영용 시스템입니다.
개발자가 아니어도, 아래 순서대로만 진행하면 운영에 필요한 기본 작업은 할 수 있도록 정리했습니다.

## 운영 환경

- Backend: `Render`
- Database: `Supabase Postgres`
- Frontend: `Vite + React`
- 운영 주기: `월 1회 배치`

쉽게 보면:

- `Render`는 백엔드 서버가 올라가는 곳입니다.
- `Supabase Postgres`는 데이터가 저장되는 DB입니다.
- `Frontend`는 실제 사용자가 보는 화면입니다.

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

즉, 운영자는 이 화면에서 "이번 달에 어디를 조정해야 하는지"를 확인하고, 필요한 경우 LTV를 수정하거나 되돌릴 수 있습니다.

## 환경 변수

처음 세팅할 때는 예시 파일을 복사한 뒤, 실제 운영값으로 바꿔 넣으면 됩니다.

```bash
cp .env.example .env
cp frontend/.env.example frontend/.env.development
```

필수:

| 변수명 | 설명 |
| --- | --- |
| `DATABASE_URL` | Supabase DB에 접속하기 위한 주소 전체 문자열 |
| `INFOCARE_USER_ID` | 경매 데이터 크롤링에 사용하는 계정 |
| `INFOCARE_PASSWORD` | 경매 데이터 크롤링에 사용하는 비밀번호 |

선택:

| 변수명 | 설명 |
| --- | --- |
| `OPENAI_API_KEY` | AI 권고안 / 챗봇을 사용할 때 필요 |
| `LTV_ADVISOR_MODEL` | 사용할 AI 모델 지정 |
| `LTV_USE_WEB_SEARCH` | AI 권고안 생성 시 웹 검색 사용 여부 |

프론트 배포 시:

```dotenv
VITE_API_BASE_URL=https://your-render-service.onrender.com
```

주의:

- DB 연결은 Supabase API 키가 아니라 `DATABASE_URL`로 합니다.
- `anon key`, `service_role key`, 프로젝트 ID만으로는 연결되지 않습니다.
- Supabase에서 필요한 것은 보통 `Connection string` 또는 `DATABASE_URL`입니다.

## AI 동작 방식

AI 기능은 크게 2가지입니다.

1. 조정 대상에 대한 `AI 권고안`
2. 화면 내 `챗봇 답변`

현재 기본 모델:

- `LTV_ADVISOR_MODEL` 기본값은 `gpt-5-nano` 입니다.
- 별도 설정이 없으면 이 모델을 기준으로 동작합니다.

### 1. AI 권고안

조정 대상이 생성되면, AI는 아래 정보를 참고해서 권고안을 만듭니다.

- 은행명
- 지역명
- 담보유형
- 현재 LTV
- 최근 3개월 / 6개월 / 12개월 평균 낙찰가율
- 최근 통계 건수
- 현재 시그널 상태(red / yellow 등)

AI가 만드는 결과는 아래 3가지가 핵심입니다.

- `conservative_ltv`: 더 보수적인 권고안
- `relaxed_ltv`: 더 완화된 권고안
- `reason`: 왜 그런 수치를 제안했는지에 대한 설명

프롬프트 로직은 아래 방향으로 구성되어 있습니다.

- 최근 지역 시장 흐름과 경매 흐름을 우선 반영
- 현재 LTV와 최근 낙찰가율 평균 차이를 함께 고려
- 너무 과격한 수치가 나오지 않도록 결과를 한 번 더 보정
- 결과는 정해진 JSON 형식으로만 받도록 제한

추가로, 설정에 따라 `LTV_USE_WEB_SEARCH=true` 인 경우에는 웹 검색 결과를 참고해 설명을 보강할 수 있습니다.

만약 AI 호출이 실패하거나 결과 형식이 맞지 않으면, 시스템은 내부 통계만 이용한 대체 권고안을 생성합니다.

### 2. 챗봇

챗봇도 같은 기본 모델 설정을 사용합니다.

챗봇은 단순히 자유 답변을 하는 방식이 아니라, 먼저 내부 데이터 조회 도구를 호출한 뒤 그 결과를 바탕으로 답변하도록 구성되어 있습니다.

예를 들어 아래와 같은 질문에 대응합니다.

- 현재 조정 대상 요약
- 특정 지역 / 담보유형 상세 조회
- 두 지역 비교
- 기준표 조회
- 시장 흐름 관련 질문

특히 시장 흐름, 전망, 원인 같은 질문이 들어오면 챗봇은 외부 뉴스 검색 도구를 먼저 호출한 뒤 답변하도록 되어 있습니다.

쉽게 말해:

- `AI 권고안`은 개별 조정 항목에 대한 수치 제안용
- `챗봇`은 운영자가 데이터를 질문하고 설명을 받는 용도

`OPENAI_API_KEY`가 없으면 이 두 기능은 정상 동작하지 않을 수 있지만, 기본적인 DB 적재와 월간 배치 자체는 진행할 수 있습니다.

## 로컬 실행

로컬에서 직접 실행해야 할 때만 사용하면 됩니다.  
이미 Render에 배포되어 있고 화면만 확인하면 되는 경우에는 이 단계가 꼭 필요하지는 않습니다.

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

처음 세팅에서는 이 3개를 순서대로 실행하면 됩니다.

역할:

- `backend/migrate_to_db.py`: 은행별 LTV 기준표를 DB에 넣습니다.
- `scripts/load_region_csvs_to_db.py`: 지역별 경매 CSV 데이터를 DB에 넣습니다.
- `scripts/build_signal_cache_from_db.py`: 화면에서 빠르게 조회할 수 있도록 집계 테이블을 만듭니다.

참고:

- `migrate_to_db.py`는 기존 DB 복사 스크립트가 아닙니다.
- 현재 `DATABASE_URL` 대상 DB에 로컬 CSV를 기준으로 적재합니다.
- 즉, "기존 DB를 가져오는 작업"이 아니라 "CSV로 새 DB를 구성하는 작업"입니다.

## 월 1회 운영

정기 운영에서는 보통 아래 명령 하나만 실행하면 됩니다.

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

쉽게 말해, 이 명령은 "이번 달 최신 데이터를 다시 수집하고 화면에 반영하는 작업"입니다.

실행 전 확인:

- `DATABASE_URL`이 비어 있지 않은지
- `INFOCARE_USER_ID`, `INFOCARE_PASSWORD`가 맞는지
- Chrome 실행 가능 여부

실행 후 확인:

- `/api/health` 정상 응답
- 대시보드가 정상적으로 열리는지
- 기준월을 바꿨을 때 데이터가 정상적으로 보이는지

## 사용자 DB로 전환

사용자 Supabase DB로 넘길 때:

1. 사용자 측 `DATABASE_URL` 확보
2. `.env`와 Render의 `DATABASE_URL`을 그 값으로 교체
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

즉, 이 방식은 "기존 DB를 통째로 복사"하는 것이 아니라, "기존 CSV를 기준으로 사용자 DB를 새로 구성"하는 방식입니다.

## 자주 쓰는 명령어

| 목적 | 명령어 |
| --- | --- |
| 새 DB 초기 적재 | `python3 backend/migrate_to_db.py` |
| 지역 CSV 전체 적재 | `python3 scripts/load_region_csvs_to_db.py` |
| signal cache 생성 | `python3 scripts/build_signal_cache_from_db.py` |
| 월간 전체 배치 | `python3 scripts/automate_full_update.py` |
| 단일 지역 크롤링 | `python3 scripts/auto_crawling.py --region 서울` |

## 장애 확인

문제가 생기면 아래 순서대로 보는 것이 가장 빠릅니다.

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
