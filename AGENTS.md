# Lapis Nexus — 에이전트 아키텍처 문서

> **업데이트**: 2026-06-11  
> **백엔드 repo**: `HAMHUIHEON/law-backend` (Railway)  
> **프론트 repo**: `HAMHUIHEON/law-frontend` (Vercel)

---

## 전체 구조

```
사용자 질문
    │
    ▼
[Frontend — Next.js /agent]
    │  HTTP POST (Clerk JWT)
    ▼
[Backend — FastAPI, Railway]
    │
    ├── /api/agent/multi   → SupervisorAgent     ← 종합 리서치
    ├── /api/agent/insight → InsightAgent        ← 판례 심층 분석
    ├── /api/prec/ask      → TaxlawPrecAgent     ← NTS 법원 판례
    └── /api/taxtr/ask     → TaxtrAgent          ← 조세심판 재결례
```

---

## 데이터 소스 현황

| 소스 | 저장소 | 건수 | 설명 |
|------|--------|------|------|
| Neo4j (국제조세 판례) | AuraDB Cloud | 판례 수백건 + 그래프 | LegalGraphSearch, 벡터 + 패턴 |
| Chroma `law_articles` | `vector_db/chroma` (로컬) | 6,687 조문 | 14개 세법 법+령+규칙 |
| Chroma `taxlaw_prec` | `vector_db/chroma` (로컬) | 32,628건 | NTS taxlaw.nts.go.kr 법원 판례 |
| Chroma `taxtr_cases` | `vector_db/chroma` (로컬) | 2,463건 | 조세심판원 재결례 |

> ⚠️ **Chroma DB는 로컬 전용**. Railway 배포 환경에는 존재하지 않아 Chroma 기반 검색은 로컬에서만 동작함.  
> Railway에서 Chroma 호출 시 500 반환 (graceful 처리).

---

## 에이전트 상세

### 1. SupervisorAgent (`MULTI`)

**파일**: `backend/agents/multi_agent.py`  
**엔드포인트**: `POST /api/agent/multi`  
**프론트 라벨**: 종합 리서치

#### 작동 방식

LangGraph `StateGraph`로 구성된 멀티 에이전트. Supervisor가 질문을 분석해 필요한 도구를 선택하고, 각 노드가 병렬로 실행된 후 Synthesizer가 통합 보고서를 생성한다.

```
START
  └─▶ supervisor  ── 질문 분석 → 사용할 도구 목록 결정 (JSON)
        ├─▶ search_cases         ── Neo4j 벡터 검색 + 승소 패턴 분석
        ├─▶ search_law           ── Chroma law_articles (14개 세법 조문)
        ├─▶ search_taxlaw_prec   ── Chroma taxlaw_prec (NTS 법원 판례 32K)
        ├─▶ search_taxtr         ── Chroma taxtr_cases (조세심판 재결례 2,463건)
        └─▶ synthesizer          ── 모든 결과 통합 → 실무 보고서
END
```

#### 도구 선택 규칙 (Supervisor 프롬프트)

| 질문 유형 | 선택되는 도구 |
|-----------|--------------|
| 판례·법원 결정·국승/국패 | `search_cases` + `search_taxlaw_prec` |
| 조세심판·재결례·이의신청 | `search_taxtr` |
| 조문·법령·규정 해석 | `search_law` |
| 일반 전략·쟁점 분석 | 4개 모두 |

#### 응답 구조

```json
{
  "query": "질문",
  "final_report": "통합 실무 보고서 (마크다운)",
  "case_context": {
    "search_results": [{ "case_number", "court_name", "judgment_date", "conclusion", "issue" }],
    "pattern_results": { "related_cases", "statutes_cited" }
  },
  "taxlaw_prec_context": [{ "case_no", "tax_type", "decision", "title", "attr_yr", "document" }],
  "taxtr_context": [{ "dem_no", "case_no", "decision", "decision_date", "title", "document" }],
  "law_articles_context": [{ "law_name", "scope", "article_no", "title", "domain", "document" }],
  "tools_used": ["search_cases", "search_taxlaw_prec", "search_taxtr", "search_law"]
}
```

#### 보고서 구성 (항상 이 순서)

1. 핵심 요약 (2~3문장)
2. 관련 법령 조문 (2~4 bullet)
3. 주요 판례·재결례 시사점 (3~6 bullet)
4. 승소 전략 포인트 (3~5 bullet)
5. 리스크 경고 (2~3 bullet)
6. 실무 체크리스트 (3~5개)

---

### 2. InsightAgent (`INSIGHT`)

**파일**: `backend/agents/insight_agent.py`  
**엔드포인트**: `POST /api/agent/insight`  
**프론트 라벨**: 판례 심층 분석

#### 작동 방식

Plan → Execute → Reflect → Report 4단계 LangGraph. 선택적으로 특정 `case_id`를 받으면 ExportC 수준 deep insight 추가.

```
START
  └─▶ planner    ── 질문 분해 → 검색 쿼리 1~3개 + 관련 법령명 추출 (JSON)
  └─▶ executor   ── Neo4j LegalGraphSearch 3종 병렬 실행
  │     ├── search_similar_issues  (쿼리별 유사 판례 벡터 검색)
  │     ├── analyze_winning_patterns (승소/패소 패턴 분석)
  │     └── get_statute_cases      (법령명별 판례 조회, statute_names 있을 때)
  └─▶ (insight)  ── case_id 제공 시 ExportC chain으로 deep insight 생성
  └─▶ critic     ── 결과 충분성 평가 → 부족 시 검색어 확장 후 executor 재시도 (최대 1회)
  └─▶ reporter   ── 모든 데이터 → 실무 보고서
END
```

#### 요청 구조

```json
{
  "query": "질문 (필수)",
  "case_id": "2023누1234 (선택 — 없으면 검색+패턴만)"
}
```

#### 응답 구조

```json
{
  "query": "질문",
  "final_report": "보고서 텍스트",
  "insight": {
    "executive_summary": {
      "one_liner": "한 줄 요약",
      "core_issues": ["핵심 쟁점 1", "2", "3"],
      "judicial_logic": { "how_the_court_thought", "legal_context" },
      "party_positions": { "taxpayer", "tax_authority", "contrasting_points" },
      "risk_view": { "taxpayer_risk", "tax_authority_risk", "precedent_signal" }
    }
  },
  "steps": ["planner", "executor", "critic", "reporter"]
}
```

---

### 3. TaxlawPrecAgent (`TAXLAW_PREC`)

**파일**: `backend/agents/taxlaw_prec_agent.py`  
**엔드포인트**: `POST /api/prec/ask`  
**프론트 라벨**: NTS 법원 판례

#### 데이터

NTS(국세청) taxlaw.nts.go.kr에서 수집한 법원 판례 32,628건.  
Chroma 컬렉션: `taxlaw_prec`  
메타데이터: `case_no`, `tax_type`, `decision`, `attr_yr`, `has_full_text`, `doc_id`, `title`

`decision` 값: `국승`, `국패`, `국일부승`, `국일부패` 등

#### 작동 방식

질문 → Chroma 벡터 검색 (top-8) → GPT-4.1 종합 답변 생성

```python
TaxlawPrecAgent().ask(question)
# → Chroma query (top-8) → GPT context + question → answer string
```

#### 추가 제공 도구 (라우터)

| 엔드포인트 | 도구 | 설명 |
|-----------|------|------|
| `GET /api/prec/stats` | `get_collection_stats` | DB 현황 (총 건수, 세목/결정 분포) |
| `POST /api/prec/search` | `search_court_cases` | 벡터 검색 (query, tax_type, decision 필터) |
| `GET /api/prec/case/{doc_id}` | `get_case_detail` | 특정 판례 전문 조회 |
| `GET /api/prec/trend` | `analyze_trend` | 연도별·결정유형 트렌드 분석 |
| `POST /api/prec/winning` | `find_winning_cases` | 사건 요약 → 유사 납세자 승소 판례 |
| `POST /api/prec/ask` | `TaxlawPrecAgent.ask()` | 자연어 질문 → GPT 답변 |

#### 요청/응답

```json
// 요청
{ "question": "이전가격 정상가격 산정 관련 국패 판례 패턴은?" }

// 응답
{
  "question": "이전가격 정상가격 산정 관련 국패 판례 패턴은?",
  "answer": "... GPT 답변 텍스트 ..."
}
```

---

### 4. TaxtrAgent (`TAXTR`)

**파일**: `backend/agents/taxtr_agent.py`  
**엔드포인트**: `POST /api/taxtr/ask`  
**프론트 라벨**: 조세심판 재결례

#### 데이터

조세심판원 재결례 2,463건.  
Chroma 컬렉션: `taxtr_cases`  
메타데이터: `dem_no`, `case_no`, `tax_type`, `decision`, `decision_date`, `related_laws`, `title`

`decision` 값: `기각`, `인용`, `취소`, `일부인용`, `경정`, `각하`, `재조사` 등

#### 작동 방식

TaxlawPrecAgent와 동일한 패턴:  
질문 → Chroma 벡터 검색 (top-8) → GPT-4.1 종합 답변

#### 추가 제공 도구 (라우터)

| 엔드포인트 | 도구 | 설명 |
|-----------|------|------|
| `GET /api/taxtr/stats` | `get_collection_stats` | DB 현황 |
| `POST /api/taxtr/search` | `search_cases` | 벡터 검색 |
| `GET /api/taxtr/case/{dem_no}` | `get_case_detail` | 특정 재결례 전문 조회 |
| `GET /api/taxtr/trend` | `analyze_trend` | 연도별·결정유형 트렌드 |
| `POST /api/taxtr/strategy` | `find_winning_strategy` | 사건 요약 → 납세자 유리 사례 |
| `POST /api/taxtr/ask` | `TaxtrAgent.ask()` | 자연어 질문 → GPT 답변 |

---

## 프론트엔드 연결

**파일**: `law-frontend/app/agent/AgentUIContext.tsx`

```typescript
// 에이전트별 엔드포인트 매핑
MULTI       → POST /api/agent/multi    { query }
INSIGHT     → POST /api/agent/insight  { query, case_id? }
TAXLAW_PREC → POST /api/prec/ask       { question }
TAXTR       → POST /api/taxtr/ask      { question }
```

**인증**: Clerk JWT (`getToken({ template: "backend-api" })`)  
**DEV_MODE**: `NEXT_PUBLIC_DEV_MODE=true` 설정 시 토큰 없이 백엔드 직접 호출

---

## 로컬 개발 환경

```powershell
# 백엔드 실행
$python = "C:\Users\LG\AppData\Local\pypoetry\Cache\virtualenvs\langchain-kr-0bF25OO7-py3.11\Scripts\python.exe"
Set-Location "C:\Users\LG\Documents\langchain-kr\29_FINAL\backend"
& $python -m uvicorn main:app --host 127.0.0.1 --port 8000

# 프론트엔드 실행 (별도 터미널)
Set-Location "C:\Users\LG\Documents\langchain-kr\29_FINAL\law-frontend"
npm run dev
```

**`.env` 위치**: `29_FINAL/.env` (backend/.env는 없음 — main.py가 `../env` 경로로 로드)

```
OPENAI_API_KEY=...
NEO4J_URI=neo4j+s://...
NEO4J_USERNAME=...
NEO4J_PASSWORD=...
CLERK_ISSUER=...
```

---

## Railway 배포 구조

- **Root directory**: `backend/`
- **Start command**: `uvicorn main:app --host 0.0.0.0 --port $PORT` (railway.toml에 명시)
- **Health check**: `GET /health` → `{"status": "ok"}`
- **주의**: Chroma DB(`vector_db/chroma`)는 Railway에 없으므로 Chroma 기반 엔드포인트는 Railway에서 500 반환

---

## 법령 벡터 DB 빌드

```powershell
# 14개 세법 전체 재빌드
$python = "C:\Users\LG\AppData\Local\pypoetry\Cache\virtualenvs\langchain-kr-0bF25OO7-py3.11\Scripts\python.exe"
& $python scripts/build_law_vector_db.py

# 초기화 후 재빌드
& $python scripts/build_law_vector_db.py --reset
```

**대상 법령 (14개)**:

| 법령 | slug | scope |
|------|------|-------|
| 국세기본법 | gukse_basic | law, decree |
| 법인세법 | corporate_tax | law, decree, rule |
| 소득세법 | income_tax | law, decree, rule |
| 부가가치세법 | vat | law, decree, rule |
| 국세징수법 | gukse_collection | law, decree, rule |
| 조세범처벌법 | tax_crime | law |
| 조세범처벌절차법 | tax_crime_proc | law, decree |
| 국제조세조정에 관한 법률 | itcl | law, decree, rule |
| 상속세 및 증여세법 | inheritance_tax | law, decree, rule |
| 관세법 | customs | law, decree, rule |
| 자본시장법 | capital_market | law, decree, rule |
| 개별소비세법 | individual_consumption | law, decree |
| 종합부동산세법 | comprehensive_realty | law, decree |
| 조세특례제한법 | joseteukrejehan | law, decree, rule |
