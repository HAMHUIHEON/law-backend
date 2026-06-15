# Lapis Nexus — Claude 작업 가이드

## 프로젝트 정체성

**프로젝트명**: Lapis Nexus  
**목적**: 한국 세법 판결문 AI 분석 시스템. 세법 조문 + 법원 판례 + 조세심판 재결례를 벡터 검색으로 연결하고, LangGraph 멀티 에이전트로 실무 보고서 생성.  
**사용자**: 세법 전문가 — 국세청 경력, 국제조세(이전가격, GLOBE) 전문, Neo4j/Python/LangChain 능숙.

---

## 일하는 원칙

1. **확인 금지**: "해도 될까요?", "진행할까요?", "확인해주세요" 절대 금지. 무엇을 할지 한 줄로 설명하고 바로 실행.
2. **근본 해결**: 우회/임시방편/회피보다 근본 원인을 파악하고 정도로 해결.
3. **긴급 승인만 질문**: 개인정보 노출, 구조 설계 갈림길(복구 불가 수준), 외부 서비스 비용 폭증 예상 시에만 물어볼 것.
4. **간결하게**: 설명은 결과 중심으로 짧게. 코드 주석은 WHY가 비자명할 때만.

---

## 기술 스택

| 구성요소 | 내용 |
|---------|------|
| DB (그래프) | Neo4j AuraDB (cloud) — `NEO4J_URI` env에서 로드 |
| DB (벡터) | ChromaDB PersistentClient — `vector_db/chroma/` (로컬 전용, Railway 없음) |
| LLM | GPT-4.1 (`gpt-4.1`) via LangChain ChatOpenAI |
| 법령 API | 법령정보센터 DRF API, OC=`seungmi0723`, HTTP (not HTTPS) |
| 백엔드 | FastAPI (`backend/main.py`) — Railway 배포 |
| 프론트 | Next.js (`law-frontend/`) — Vercel 배포 (전체화면 검색 UI, 9종 에이전트) |
| 에이전트 프레임워크 | LangGraph StateGraph |
| 판결문 파이프라인 | `bravo/` — 10단계 구조화 파이프라인 |
| Python 환경 | pypoetry venv `C:\Users\LG\AppData\Local\pypoetry\Cache\virtualenvs\langchain-kr-0bF25OO7-py3.11\Scripts\python.exe` |

---

## 배포 현황

| 서비스 | 플랫폼 | URL | 상태 |
|-------|--------|-----|------|
| Backend API | Railway | `https://law-backend-production-5249.up.railway.app` | 정상 — Volume Chroma 포함 9종 에이전트 전부 동작 |
| Frontend | Vercel | — | 정상 — 전체화면 검색 UI (`59490b5`) |

**Railway 구성**:
- Root directory: `backend/`
- `backend/railway.toml`에 startCommand, healthcheck 명시
- `.env` 위치: `29_FINAL/.env` (backend/.env 없음 — main.py가 `../env` 로드)

---

## 프로젝트 구조 (핵심 폴더만)

```
29_FINAL/
├── CLAUDE.md                 # 이 파일
├── AGENTS.md                 # 에이전트 아키텍처 상세 문서
├── .env                      # 환경변수 (git 제외)
├── backend/                  # Railway 배포 루트
│   ├── main.py               # FastAPI 진입점 (Clerk 인증, 라우터 등록, /health)
│   ├── railway.toml          # Railway 배포 설정
│   ├── requirements.txt      # Python 의존성
│   ├── agents/               # LangGraph 에이전트
│   │   ├── insight_agent.py  # InsightAgent (Plan→Execute→Reflect→Report)
│   │   ├── multi_agent.py    # SupervisorAgent (4개 소스 멀티 에이전트)
│   │   ├── taxlaw_prec_agent.py  # TaxlawPrecAgent (NTS 법원 판례 32,628건)
│   │   └── taxtr_agent.py    # TaxtrAgent (조세심판 재결례 2,463건)
│   ├── routers/              # FastAPI 라우터
│   │   ├── agent.py          # /api/agent/insight, /api/agent/multi
│   │   ├── taxlaw_prec.py    # /api/prec/*
│   │   ├── taxtr.py          # /api/taxtr/*
│   │   ├── cases.py          # /api/cases/* (Neo4j 판례)
│   │   └── law.py            # /api/law/* (법령 조문)
│   ├── db/
│   │   ├── graph_ingest.py   # Neo4j 인제스트
│   │   └── itcl_search.py    # Neo4j LegalGraphSearch (InsightAgent용)
│   └── cache/                # LLM 결과 캐시 607건 (git 제외)
├── vector_db/chroma/         # ChromaDB 로컬 (git 제외)
│   ├── law_articles/         # 6,687 조문 (14개 세법 법+령+규칙)
│   ├── taxlaw_prec/          # 32,628 NTS 법원 판례
│   └── taxtr_cases/          # 2,463 조세심판 재결례
├── law/                      # 법령 JSON DB (635MB, git 제외)
│   ├── gukse_basic/          # 국세기본법
│   ├── corporate_tax/        # 법인세법
│   ├── income_tax/           # 소득세법
│   ├── vat/                  # 부가가치세법
│   ├── gukse_collection/     # 국세징수법
│   ├── tax_crime/            # 조세범처벌법
│   ├── tax_crime_proc/       # 조세범처벌절차법
│   ├── itcl/                 # 국제조세조정법
│   ├── inheritance_tax/      # 상속세 및 증여세법
│   ├── customs/              # 관세법
│   ├── capital_market/       # 자본시장법
│   ├── individual_consumption/ # 개별소비세법
│   ├── comprehensive_realty/ # 종합부동산세법
│   └── joseteukrejehan/      # 조세특례제한법
├── cases/                    # 원본 판례 데이터
│   ├── court_api/            # 법원 API 696건
│   ├── taxtr/                # 조세심판 재결례 2,463건
│   └── inquiry/              # 질의회신 (미수집)
├── scripts/                  # 빌드·운영 스크립트
│   ├── build_law_vector_db.py      # Chroma law_articles 빌드
│   ├── build_law_history_db.py     # DRF API 전체 다운로드
│   └── run_court_pipeline_parallel.py  # bravo 파이프라인 병렬 실행
├── ITCL/                     # 국제조세조정법 처리 파이프라인
├── ITCL_integrated/          # 법+령+규칙 통합 분석
└── law-frontend/             # Next.js 프론트엔드 (Vercel)
    └── app/agent/            # 에이전트 전용 UI (9개 에이전트, 전체화면 검색 스타일)
```

---

## Chroma DB 현황 (로컬, `vector_db/chroma`)

| 컬렉션 | 건수 | 소스 | 빌드 스크립트 |
|--------|------|------|--------------|
| `law_articles` | 6,687 조문 | `law/` 폴더 14개 세법 | `scripts/build_law_vector_db.py` |
| `taxlaw_prec` | 32,628건 | NTS taxlaw.nts.go.kr | (별도 스크래핑) |
| `taxtr_cases` | 2,463건 | 조세심판원 | (별도 스크래핑) |

> ⚠️ Chroma DB는 **로컬 전용**. Railway 배포 환경에는 없음.  
> Railway에서 Chroma 호출 시 500 반환.

---

## 에이전트 구성 (2026-06-13 기준)

상세 내용은 `AGENTS.md` 참조.

| 에이전트 | 파일 | 엔드포인트 | 데이터 소스 |
|---------|------|-----------|------------|
| SupervisorAgent (MULTI) | `agents/multi_agent.py` | `POST /api/agent/multi` | Neo4j + Chroma 3종 |
| InsightAgent (INSIGHT) | `agents/insight_agent.py` | `POST /api/agent/insight` | Neo4j + Chroma law_articles |
| TaxlawPrecAgent | `agents/taxlaw_prec_agent.py` | `POST /api/prec/ask` | Chroma `taxlaw_prec` |
| TaxtrAgent | `agents/taxtr_agent.py` | `POST /api/taxtr/ask` | Chroma `taxtr_cases` |
| StrategyAgent | `agents/strategy_agent.py` | `POST /api/strategy/strategy` | Chroma 3종 |
| RebuttalAgent | `agents/rebuttal_agent.py` | `POST /api/strategy/rebuttal` | Chroma 2종 (승소 필터) |
| TrendAgent | `agents/trend_agent.py` | `POST /api/trend/ask` | Chroma taxlaw_prec 연도집계 |
| ITCLAgent | `agents/itcl_agent.py` | `POST /api/itcl/ask` | Chroma 2종 + Neo4j ITCLSearch |
| RiskAgent | `agents/risk_agent.py` | `POST /api/strategy/risk` | Chroma 3종 |

**MULTI 에이전트 검색 소스 (7개)**:
1. `search_cases` — Neo4j 벡터 검색 (국제조세 판례)
2. `search_law` — Chroma `law_articles` (14개 세법 조문 6,687건)
3. `search_taxlaw_prec` — Chroma `taxlaw_prec` (NTS 법원 판례 32,628건)
4. `search_taxtr` — Chroma `taxtr_cases` (조세심판 재결례 2,463건)
5. `search_issue_cache` — `issue_index/issue_vectors.pkl` (사전 분석 판례 쟁점 벡터, 1021건·270판례)
6. `search_pdf_cases` — Chroma `pdf_court_cases` (PDF 원본 판결문 560건)
7. `search_inquiry` — Chroma `inquiry_cases` (국세청 질의회신 119,427건 — Volume 재업로드 필요)

---

## Neo4j 그래프 스키마

### Norm 레이어 (법/령/규칙 구조)

```
Law (scope, id)
└─ HAS_VERSION
   └─ LawVersion (scope, law_id, version_key)   ← version_key = "공포일자_공포번호"
      └─ HAS_CHAPTER
         └─ Chapter (scope, law_id, version_key, id)   ← law_id 포함 (충돌 방지)
            └─ HAS_SECTION / HAS_ARTICLE
               └─ Article (scope, law_id, version_key, id)
                  ├─ HAS_PARAGRAPH → Paragraph
                  └─ NORM_UNIT_OF ← NormUnit
                       └─ REFERS_TO → LawTarget → RESOLVES_TO → Article
```

**⚠️ 중요**: Chapter/Article 등 중간 노드에 `law_id` 포함. 다중 법령 공존 시 충돌 방지.  
`scope` = "LAW" | "DECREE" | "RULE"

### Integrated 레이어 (통합 의미 분석 — ITCL 65개 버전)

```
IntegratedSnapshot (scope="INTEGRATED", set_key)
└─ HAS_INTEGRATED_CHAPTER
   └─ IntegratedChapter
      ├─ DERIVED_FROM → Chapter (LAW/DECREE/RULE)
      ├─ HAS_INTEGRATED_SEMANTIC → SemanticIssue
      └─ HAS_INTEGRATED_REASONING → ReasoningIssue
           └─ HAS_STEP → ReasoningStep → BASED_ON → Article
```

---

## 로컬 개발 환경

```powershell
# poetry venv Python 경로 확인
poetry env info --path

# 백엔드 실행 (29_FINAL/backend/)
& "<poetry-venv>\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8000

# 프론트엔드 실행 (29_FINAL/law-frontend/, 별도 터미널)
npm run dev
```

**주의**: `python-jose`가 poetry venv에 없을 수 있음 → `pip install "python-jose[cryptography]"` 수동 설치 필요.

## Git 작업 주의사항

`29_FINAL/`은 `HAMHUIHEON/law-backend`에 연결된 **별도 git 레포**. 백엔드 커밋/push는 반드시 `29_FINAL/` 안에서 실행.  
상위 `langchain-kr/` 폴더는 teddylee777 강의 레포 — 백엔드와 무관.

```powershell
cd 29_FINAL/
git add backend/...
git commit -m "..."
git push origin main   # main 브랜치 (master도 동기 유지)
```

---

## 법령 벡터 DB 빌드

```powershell
$python = "C:\Users\LG\AppData\Local\pypoetry\Cache\virtualenvs\langchain-kr-0bF25OO7-py3.11\Scripts\python.exe"
# 전체 재빌드
& $python scripts/build_law_vector_db.py
# 초기화 후 재빌드
& $python scripts/build_law_vector_db.py --reset
```

---

## 법령정보센터 DRF API

```python
# MST 목록 (HTML 파싱)
GET http://www.law.go.kr/DRF/lawSearch.do
  ?OC={YOUR_OC}&target=lsHistory&type=HTML&query={법령명}&display=100&page=1

# 개별 버전 JSON
GET http://www.law.go.kr/DRF/lawService.do
  ?OC={YOUR_OC}&target=law&MST={mst}&type=JSON
```

**주의**: `requests.Session()` 재사용 시 ConnectionResetError 발생. 요청마다 새 Session 생성 필수.

---

## 환경변수 (`.env` — `29_FINAL/.env`)

```
OPENAI_API_KEY=...
NEO4J_URI=neo4j+s://a0c49c04.databases.neo4j.io
NEO4J_USERNAME=...
NEO4J_PASSWORD=...
CLERK_ISSUER=https://...clerk.accounts.dev
```

---

## 완료 현황 (2026-06-15)

| 항목 | 상태 | 내용 |
|------|------|------|
| Railway 502 근본 해결 | ✅ | `agents/__init__.py` 비움 (cascade import 제거) |
| 프론트엔드 UI 전체화면 재설계 | ✅ | Google 검색 스타일, 9종 칩 선택 — Vercel `59490b5` 배포 |
| chroma_search 임베딩 버그 수정 | ✅ | text-embedding-3-small EF 명시 (`e4e571ef`) |
| Railway Chroma Volume 배포 | ✅ | 265MB zip → Volume /app/chroma (taxlaw_prec 32,628건 등) |
| Railway Chroma 검색 동작 | ✅ | openai v1.x 호환 커스텀 EF 사용 (`475e0e2d`) |
| ITCL 법령 Chroma 통합 | ✅ | v2 아카이브에 이미 포함 확인. cold-start `add_itcl_to_chroma.py` 추가 |
| MULTI 캐시 쟁점 검색 추가 | ✅ | `search_issue_cache` 5번째 도구 — issue_index 1021건 로드 (`865f5d41`) |
| MULTI PDF 판례 검색 추가 | ✅ | `search_pdf_cases` 6번째 도구 — pdf_court_cases 560건, ITCL 키워드 우선순위 (`3655d799`) |
| MULTI UI PDF/쟁점 섹션 추가 | ✅ | `pdf_cases_context` + `issue_cache_context` 렌더링 — Vercel `e406fdf` 배포 |
| Vercel API_BASE 수정 | ✅ | `.env.production` + fallback 코드 → Railway URL. lapis.nexus 정상 확인 (`50ee8ca`, `09a0419`) |
| `mcp_server.py` 에이전트 툴 완성 | ✅ | 17개 툴 전부 구현. `run_supervisor_agent` 설명 6개 소스로 업데이트 |
| MULTI inquiry_cases 7번째 도구 추가 | ✅ | `search_inquiry` 노드 — 국세청 질의회신 벡터 검색. 프론트엔드 UI 섹션 추가 (`94f7110c`, `489c473`) |
| taxlaw_prec/taxtr CHROMA_DIR 버그 수정 | ✅ | 두 에이전트가 CHROMA_DIR env 무시하던 버그 수정 → `/api/prec/stats` 200 OK (`79b778e3`) |
| init_chroma.py Volume 보호 강화 | ✅ | 버전파일 단독으로 스킵 판단. 동일/상위 버전 마킹 시 URL 재다운로드 차단 (`fb6e527f`) |

## Railway Volume 현재 상태 (2026-06-15)

| 컬렉션 | 건수 | 상태 |
|--------|------|------|
| taxlaw_prec | 32,628건 | ✅ 정상 |
| taxtr_cases | 2,463건 | ✅ 정상 |
| law_articles | 6,660건 | ✅ 정상 |
| pdf_court_cases | 560건 | ✅ 정상 |
| itcl | (ITCL 법령) | ✅ 정상 |
| inquiry_cases | **0건** | ⚠️ 미복구 — SSH 재업로드 필요 |

**Volume 마킹**: `.chroma_version = "v4"` 기록됨 → 다음 배포부터 URL 재다운로드 차단됨  
**Volume 경로**: `/app/chroma` (CHROMA_DIR env var), `law-backend-volume` 마운트

## inquiry_cases 복구 방법 (사용자 액션 필요)

1차 선택 (권장): **GitHub Release v4 업로드**
```bash
# 1. GitHub Releases에서 chroma-v4 release 생성 (https://github.com/HAMHUIHEON/law-backend/releases/new)
# 2. chroma_v4.tar.gz (1010MB) 업로드
# 3. Railway env 업데이트:
railway variables set CHROMA_DOWNLOAD_URL=https://github.com/HAMHUIHEON/law-backend/releases/download/chroma-v4/chroma_v4.tar.gz
# 4. init_chroma.py의 CHROMA_VERSION = "v5" 로 업데이트 (Volume 강제 교체)
```

2차 선택: **SSH 직접 업로드 (기존 방식)**
```bash
# Railway SSH config가 ~/.ssh/config에 있다고 가정
scp chroma_v4.tar.gz railway-law-backend:/tmp/
ssh railway-law-backend "rm -rf /app/chroma && mkdir -p /app/chroma && tar xzf /tmp/chroma_v4.tar.gz -C /app/chroma --strip-components=1 && echo 'v4' > /app/chroma/.chroma_version && rm /tmp/chroma_v4.tar.gz"
# ⚠️ 반드시 echo 'v4' > .chroma_version 포함 — 없으면 다음 deploy에 재다운로드됨
```

## Chroma 검색 중요 주의사항

`db/chroma_search.py`의 모든 `get_collection` 호출에 `embedding_function=_get_ef()` 필수.  
Chroma 기본(ONNX 384-dim) ≠ 빌드 시 사용한 OpenAI text-embedding-3-small(1536-dim) → 쿼리 전부 예외 발생.

## 다음 세션 시작 항목

1. **inquiry_cases 복구** — 위 SSH 업로드 또는 GitHub Release v4 방법으로 복구 (v4.tar.gz 로컬에 있음)
2. **bravo 43건 미처리** — `scripts/run_court_pipeline_parallel.py --workers 4` 재실행
3. **Neo4j 7개 세법 인제스트 (LAW_7)** — 장기 과제

## issue_index 구조 (캐시 쟁점 벡터 인덱스)

`backend/issue_index/issue_vectors.pkl` — 12.4MB, 1021 쟁점 행, 270 판례  
`backend/issue_index/issue_vectors_meta.json` — case_id → 파일 해시 (증분 빌드용)  
`backend/issue_vector_index.py` — 빌드(`build_index()`) · 로드(`load_index()`) · 검색(`search()`)  
`backend/cache/` — 원본 판례 분석 JSON (git 제외, 로컬+Railway Volume에만 존재)

인덱스 재빌드: `python issue_vector_index.py` (로컬에서만, `cache/` 필요)
