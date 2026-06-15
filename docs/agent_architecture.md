# Lapis Nexus — 에이전트 아키텍처 상세 문서

> 작성: 2026-06-16  
> 대상 독자: 프로젝트 소유자 (세법 전문가 + AI 개발자)

---

## 전체 구조 한눈에 보기

```
사용자 질문
    │
    ▼
[Vercel Frontend] — 10종 에이전트 칩 선택 + 입력
    │  POST /api/{endpoint}
    ▼
[Railway FastAPI Backend]
    │
    ├─ MULTI        → supervisor → 7개 병렬 노드 → synthesizer → 보고서
    ├─ INSIGHT      → planner → executor → insight → critic → reporter → 보고서
    ├─ TAXLAW_PREC  → 5개 Tool → LLM → 답변
    ├─ TAXTR        → 5개 Tool → LLM → 답변
    ├─ STRATEGY     → fact_extractor → case_searcher → strategist → 전략 보고서
    ├─ REBUTTAL     → claim_extractor → case_searcher → draft_writer → reflector → 초안
    ├─ TREND        → data_collector → trend_analyzer → 트렌드 보고서
    ├─ ITCL         → searcher → analyzer → 이전가격 분석 보고서
    ├─ RISK         → case_finder → risk_evaluator → 소송 리스크 보고서
    └─ LAW_RISK     → RiskAgent.ask() → 법령 개정 분석 답변
```

---

## 데이터 소스 지도

| # | 소스 | 위치 | 건수 | 임베딩 |
|---|------|------|------|--------|
| 1 | Neo4j 판례 그래프 | AuraDB Cloud | Case 수백+, Paragraph 112K, Article 41K | text-embedding-3-small (1536d) |
| 2 | Chroma `taxlaw_prec` | Railway Volume `/app/chroma` | 32,628건 | text-embedding-3-small |
| 3 | Chroma `taxtr_cases` | Railway Volume | 2,463건 | text-embedding-3-small |
| 4 | Chroma `law_articles` | Railway Volume | 6,660조문 | text-embedding-3-small |
| 5 | Chroma `pdf_court_cases` | Railway Volume | 560건 | text-embedding-3-small |
| 6 | Chroma `inquiry_cases` | Railway Volume | 119,427건 | text-embedding-3-small |
| 7 | `issue_index/issue_vectors.pkl` | Docker 이미지 포함 | 1,021쟁점 / 270판례 | text-embedding-3-small |
| 8 | `law/` 폴더 JSON | Railway `/app/law` | 14개 세법 × 3종류 최신 버전 | - (JSON 원문) |

---

## Agent 1: 종합 리서치 (MULTI)

**파일**: `backend/agents/multi_agent.py`  
**엔드포인트**: `POST /api/agent/multi`  
**칩 색상**: `#1e40af` (파랑)

### 입력

```json
{
  "query": "이전가격 과소신고 관련 판례와 세법 조문을 종합 분석해줘",
  "messages": []
}
```

### LangGraph 흐름

```
START
  └─▶ supervisor
        ├─ (동시 실행) case_search_node       → Neo4j 벡터 검색 (판례·패턴)
        ├─ (동시 실행) law_search_node         → Chroma law_articles (6,660조문)
        ├─ (동시 실행) taxlaw_prec_node        → Chroma taxlaw_prec (32,628건)
        ├─ (동시 실행) taxtr_node              → Chroma taxtr_cases (2,463건)
        ├─ (동시 실행) inquiry_node            → Chroma inquiry_cases (119,427건)
        ├─ (동시 실행) issue_cache_node        → issue_vectors.pkl (1,021쟁점)
        └─ (동시 실행) pdf_cases_node          → Chroma pdf_court_cases (560건)
              └─▶ synthesizer → 종합 보고서
END
```

### supervisor 핵심 로직

- 7개 도구를 항상 전부 실행 (LLM이 선택하지 않음 — 실패 방지)
- **ITCL/이전가격 감지**: 쿼리에 `이전가격`, `국제조세`, `BEPS`, `필라`, `CFC` 등 22개 키워드 중 하나라도 있으면 → 고정 전문 쿼리 사용
- **일반 쿼리**: LLM이 소스별 최적화 검색 쿼리 생성  
  (`law_query`, `prec_query`, `taxtr_query`, `inquiry_query` 4개를 JSON으로 반환)

### synthesizer 보고서 구성

1. 핵심 쟁점 요약
2. 법원 판례 분석 (taxlaw_prec + pdf + Neo4j)
3. 조세심판 재결례 분석
4. 관련 세법 조문
5. 질의회신 참고 사례
6. 최종 종합 의견

### 출력 키

```
final_report            (str) 마크다운 보고서
case_context            (dict) Neo4j 검색 결과
taxlaw_prec_context     (list) 법원 판례
taxtr_context           (list) 재결례
law_articles_context    (list) 세법 조문
inquiry_cases_context   (list) 질의회신
issue_cache_context     (list) 구조화 쟁점 캐시
pdf_cases_context       (list) PDF 판례
tools_used              (list) 실행된 도구 목록
```

---

## Agent 2: 판례 심층 분석 (INSIGHT)

**파일**: `backend/agents/insight_agent.py`  
**엔드포인트**: `POST /api/agent/insight`  
**칩 색상**: `#7c3aed` (보라)

### 입력

```json
{
  "query": "부당행위계산부인 적용 기준에 관한 판례 전략 보고서를 작성해줘",
  "case_id": null
}
```

`case_id`를 지정하면 해당 판례 원문(`cache/`)을 기반으로 ExportC 수준 심층 분석 추가.

### LangGraph 흐름

```
START
  └─▶ planner_node
        (LLM: 쿼리 → 쟁점별 검색어 1~3개 + 법령명 추출)
  └─▶ executor_node
        ├─ Neo4j search_similar_issues  (쟁점별 유사 판례, top_k=5×쟁점수)
        ├─ Neo4j analyze_winning_patterns (승소/패소 패턴 분석, top_k=10)
        └─ Chroma law_articles          (법령명+쿼리 조합 검색, n=6)
  └─▶ insight_node          (case_id 있을 때만: ExportC deep analysis)
  └─▶ critic_node
        판단 기준:
          (1) 결과 3건 미만 → retry
          (2) 최고 유사도 < 0.60 → retry
          (3) 0.60~0.70 구간: LLM이 관련성 직접 판단
        → "retry" 시 executor 재실행 (최대 2회)
  └─▶ reporter_node         (최종 실무 보고서 생성)
END
```

### Planner가 생성하는 것

입력: `"부당행위계산부인 적용 기준에 관한 판례 전략 보고서"`  
출력:
```json
{
  "search_queries": [
    "특수관계자간 자산 저가양도 부당행위계산 부인 요건",
    "시가 산정 기준 부당행위계산 적용 범위"
  ],
  "statute_names": ["법인세법", "소득세법"]
}
```

### Reporter 보고서 구성

1. 사건 배경 및 쟁점
2. 핵심 판례 분석 (번호·결론·법리 근거 포함)
3. 승소/패소 패턴
4. 관련 법령 조문
5. 실무 전략 제언

### 출력 키

```
final_report       (str) 마크다운 보고서
insight            (dict) ExportC 심층 분석 (case_id 있을 때)
law_articles_context (list)
steps              (list) 단계별 실행 로그
```

---

## Agent 3: 법원 판례 검색 (TAXLAW_PREC)

**파일**: `backend/agents/taxlaw_prec_agent.py`  
**엔드포인트**: `POST /api/prec/ask`  
**칩 색상**: `#065f46` (초록)

### 입력

```json
{
  "question": "명의신탁 증여세 과세처분 관련 법원 판례를 찾아줘",
  "messages": []
}
```

### 구조

LangGraph 없이 **LangChain Tool Use** 방식:

```
질문 → LLM(Tool 선택) → Tool 실행 → LLM(최종 답변)
```

### 5개 Tool

| Tool | 기능 |
|------|------|
| `get_collection_stats` | DB 현황 (총 건수, 세목 분포, 결정 유형) |
| `search_court_cases` | 키워드 검색 (filter: tax_type, decision, n_results) |
| `get_case_details` | 판례 원문 조회 (doc_id) |
| `analyze_cases` | 쟁점 분석 → LLM 요약 |
| `search_by_tax_type` | 세목별 필터 검색 |

### 데이터

- Chroma `taxlaw_prec`: NTS taxlaw.nts.go.kr 법원 판례 **32,628건**
- 메타데이터: `tax_type`, `decision` (국승/국패/일부국패/각하), `case_no`, `attr_yr`

### 출력

```json
{
  "question": "...",
  "answer": "## 명의신탁 증여세 판례 분석\n\n..."
}
```

---

## Agent 4: 조세심판 재결례 (TAXTR)

**파일**: `backend/agents/taxtr_agent.py`  
**엔드포인트**: `POST /api/taxtr/ask`  
**칩 색상**: `#92400e` (갈색)

### 입력

```json
{
  "question": "경비 부인 처분에 대한 조세심판 재결례를 분석해줘",
  "messages": []
}
```

### 구조

TAXLAW_PREC와 동일한 Tool Use 방식.

### 5개 Tool

| Tool | 기능 |
|------|------|
| `get_taxtr_stats` | DB 현황 |
| `search_taxtr_cases` | 키워드 검색 (filter: decision_type=인용/기각/각하) |
| `get_taxtr_details` | 재결례 원문 |
| `analyze_taxtr_cases` | 쟁점 분석 |
| `search_by_decision` | 인용/기각 필터 검색 |

### 데이터

- Chroma `taxtr_cases`: 조세심판원 재결례 **2,463건**
- 메타데이터: `case_no` (조심번호), `decision` (인용/기각/각하), `tax_type`, `dem_no`

---

## Agent 5: 불복전략 분석 (STRATEGY)

**파일**: `backend/agents/strategy_agent.py`  
**엔드포인트**: `POST /api/strategy/strategy`  
**칩 색상**: `#0f766e` (청록)

### 입력

```json
{
  "query": "이전가격 과세처분을 받았습니다. 불복 전략을 분석해줘",
  "disposition_date": "2025-10-01",
  "tax_amount": "5억원",
  "already_filed": false
}
```

### LangGraph 흐름

```
START
  └─▶ fact_extractor    (LLM: 사건 요약 → 세목, 쟁점, 처분 유형 추출)
  └─▶ case_searcher     (Chroma 3종 검색: taxlaw_prec + taxtr_cases + law_articles)
  └─▶ strategist        (LLM: 불복 경로 선정 + 전략 보고서)
END
```

### 불복 기한 자동 계산

`disposition_date` 입력 시 `_compute_deadlines()` 자동 실행:

```
이의신청_마감: 처분일 + 90일
심판청구_마감: 처분일 + 90일
행정소송_마감: 심판청구 결정 통지일로부터 90일
경정청구_마감: 법정신고기한으로부터 5년 (국세기본법 제45조의2)
```

### 출력 키

```
final_report   (str) 전략 보고서
court_cases    (list) 유사 판례
taxtr_cases    (list) 유사 재결례
law_articles   (list) 관련 조문
deadlines      (dict) 불복 기한 날짜별 정리
```

---

## Agent 6: 반론 초안 작성 (REBUTTAL)

**파일**: `backend/agents/rebuttal_agent.py`  
**엔드포인트**: `POST /api/strategy/rebuttal`  
**칩 색상**: `#c2410c` (주황)

### 입력

```json
{
  "disposition_text": "과세처분 이유서 전문 붙여넣기...",
  "filing_type": "심판청구",
  "taxpayer_name": "주식회사 한국무역",
  "taxpayer_id": "123-45-67890",
  "tax_office": "서울지방국세청장",
  "disposition_date": "2025-10-01",
  "tax_amount": "150000000",
  "tax_type": "법인세"
}
```

파일 업로드도 지원: `POST /api/strategy/rebuttal/upload` (PDF/TXT)

### LangGraph 흐름

```
START
  └─▶ claim_extractor_node
        (LLM: 과세처분 이유서 → 과세관청 주장 + 반론해야 할 핵심 쟁점 JSON 추출)
  └─▶ case_searcher_node
        ├─ Chroma taxlaw_prec: 납세자 승소 필터 (filter_winning=True), n=10
        ├─ Chroma taxtr_cases: 인용 필터 (filter_favorable=True), n=6
        └─ Chroma law_articles: 세법 조문, n=6
  └─▶ draft_writer_node
        (LLM: 법적 문서 형식의 청구서 초안 작성)
        생성 헤더: 청구인/처분청/제출기관/세목/처분일/처분액/청구기한
  └─▶ reflector_node    (최대 2회: 논거 보완 여부 판단 + 재작성)
END
```

### Citation Guard

- 초안에 포함된 판례번호를 regex로 추출
- 실제 검색된 판례 목록과 교차 검증
- 미검증 번호는 `[검증필요: 2020두12345]` 형태로 표시

### 출력 키

```
final_report           (str) 이의신청서/심판청구서/소장 초안
winning_court_cases    (list) 납세자 승소 판례
favorable_taxtr_cases  (list) 인용 재결례
law_articles           (list) 근거 조문
unverified_citations   (list) 검증 필요 판례번호
deadlines              (dict) 불복 기한
```

---

## Agent 7: 판례 트렌드 (TREND)

**파일**: `backend/agents/trend_agent.py`  
**엔드포인트**: `POST /api/trend/ask`  
**칩 색상**: `#0369a1` (하늘)

### 입력

```json
{
  "query": "최근 5년간 부가세 매입세액 공제 거부 판례 트렌드를 분석해줘",
  "messages": []
}
```

### LangGraph 흐름

```
START
  └─▶ data_collector_node
        ├─ get_taxlaw_prec_stats(query, n=100) → 연도별 통계 (attr_yr 기반)
        │   year_stats: {
        │     "2020": {"total": 45, "taxpayer_win": 12, "win_rate": 26.7},
        │     "2021": {"total": 52, ...},
        │     ...
        │   }
        └─ search_taxtr_cases(query, n=10) → 조세심판 재결례 샘플
  └─▶ trend_analyzer_node
        (LLM: 연도별 데이터 + 재결례 샘플 → 트렌드 보고서)
END
```

### 보고서 구성

1. 연도별 납세자 승소율 변화 (표 포함)
2. 법리 변천사 및 주요 판례 흐름
3. 최근 조세심판 경향
4. 실무 시사점

### 출력 키

```
final_report  (str)
trend_data    {total_cases, year_stats, sample}
taxtr_sample  (list)
```

---

## Agent 8: 국제조세 분석 (ITCL)

**파일**: `backend/agents/itcl_agent.py`  
**엔드포인트**: `POST /api/itcl/ask`  
**칩 색상**: `#6d28d9` (진보라)

### 입력

```json
{
  "query": "GLOBE 필라2 세액공제 관련 국제조세 판례와 법령을 분석해줘",
  "transaction_type": "무형자산 라이선스",
  "related_party_country": "미국",
  "transaction_amount_krw": 5000000000,
  "transaction_year": "2024"
}
```

### 거래 유형별 우선 방법 (OECD TPG 2022 기준)

| 거래 유형 | 우선 적용 방법 |
|-----------|--------------|
| 유형자산 매각 | CUP, TNMM |
| 무형자산 양도 | CUP, PSM |
| 무형자산 라이선스 | CUP, PSM |
| 용역 제공 | COST+, TNMM |
| 금전 대여/차입 | CUP (정상이자율) |
| 원자재·완제품 매매 | CUP, RPM |
| 기타 | TNMM |

APA 적합 기준: 거래 금액 **50억원 이상**

### LangGraph 흐름

```
START
  └─▶ searcher_node
        ├─ Chroma taxlaw_prec  → 이전가격 관련 법원 판례 (n=8)
        ├─ Chroma law_articles → 국조법·세법 조문 (n=8)
        └─ Neo4j ITCLSearch    → ITCL IntegratedSnapshot 65개 버전 쟁점 검색 (실패 시 스킵)
  └─▶ analyzer_node
        (LLM: 거래 유형별 최적 방법 + APA 가이드 + 조세조약 참고 + 판례 분석)
END
```

### 보고서 구성

1. 정상가격 산출 방법 권고 (우선순위 + 각 방법 장단점)
2. APA 사전 신청 적합 여부
3. 관련 판례 분석
4. 조세조약 고려 사항 (국가 지정 시)
5. 리스크 평가

### 출력 키

```
final_report       (str)
court_cases        (list)
law_articles       (list)
itcl_issues        (list) Neo4j ITCL 쟁점
preferred_methods  (list) 거래 유형별 권장 방법
transaction_type   (str)
```

---

## Agent 9: 개정법령 리스크 (RISK)

**파일**: `backend/agents/risk_agent.py`  
**엔드포인트**: `POST /api/strategy/risk`  
**칩 색상**: `#b91c1c` (빨강)

### 입력

```json
{
  "statute_name": "조세특례제한법",
  "revision_summary": "2025년 R&D 세액공제 요건 강화, 일몰 연장",
  "effective_date": "2026-01-01"
}
```

### LangGraph 흐름

```
START
  └─▶ case_finder_node
        검색 쿼리: statute_name + revision_summary[:100]
        ├─ Chroma taxlaw_prec  → 해당 법령 관련 판례 (n=15)
        ├─ Chroma taxtr_cases  → 관련 재결례 (n=8)
        └─ Chroma law_articles → 개정 대상 조문 (n=6)
  └─▶ risk_evaluator_node
        (LLM: 개정 내용 기반 기존 판례 유효성 재평가 + 리스크 보고서)
END
```

### 보고서 구성

1. 개정 핵심 내용
2. 영향받는 기존 판례 (유효성 변화 여부)
3. 조세심판 재결례 영향 분석
4. 개정 대상 조문
5. 실무 대응 권고

### 출력 키

```
final_report          (str)
affected_court_cases  (list)
affected_taxtr_cases  (list)
revised_articles      (list)
```

---

## Agent 10: 법령개정 분석 (LAW_RISK)

**파일**: `backend/RISK/agent.py` + `RISK/consulting.py`  
**엔드포인트**: `POST /api/risk/ask`  
**칩 색상**: `#7e22ce` (진보라)

### 입력

```json
{
  "question": "법인세법이 최근에 어떻게 바뀌었나요?"
}
```

### 구조 (LangGraph 없음 — 단순 파이프라인)

```
question
  └─▶ 법령명 감지
        지원 14개 세법 중 (긴 이름 우선 매칭):
        국제조세조정에 관한 법률, 법인세법, 소득세법, 부가가치세법, 국세기본법,
        국세징수법, 조세범처벌법, 조세범처벌절차법, 상속세 및 증여세법, 관세법,
        자본시장과 금융투자업에 관한 법률, 개별소비세법, 종합부동산세법, 조세특례제한법
  └─▶ run_full_analysis(법령명, "LAW")
        ├─ _load_version_index()    → /app/law/{slug}/law/_version_index.json 로드
        ├─ _load_converted()        → 최신 버전 JSON → DRF raw → converted 형식 변환
        │     변환 내용:
        │       amendments ← 개정문.개정문내용
        │       revision_reasons ← 제개정이유.제개정이유내용
        │       addenda ← 부칙단위[].{부칙공포일자, 부칙내용}
        ├─ RevisionObservationChain → 개정 관측 (observed_changes, notes)
        └─ AddendaObservationChain  → 부칙 분석 (부칙별 시행 조건)
  └─▶ GPT 답변 생성
        [분석 결과] + [질문] → 실무 리스크 답변
```

### 법령 데이터 구조 (`/app/law/`)

```
law/
├── corporate_tax/law/
│   ├── _version_index.json   {pno: {version_key, pdate, pno, eff_date, mst, file}}
│   └── {mst}.json            DRF API raw JSON (개정문+제개정이유+부칙 포함)
├── income_tax/...
└── ...
```

**초기화**: Railway cold-start 시 `init_law.py` → `law_latest.tar.gz` (5.8MB) 다운로드·설치

### 출력

```json
{
  "question": "법인세법이 최근에 어떻게 바뀌었나요?",
  "answer": "## 법인세법 최근 개정 분석\n\n..."
}
```

---

## 공통 패턴

### Lazy Init (Railway 필수)

모든 에이전트가 공통으로 준수하는 패턴:

```python
_llm = None

def _get_llm():
    global _llm
    if _llm is None:
        _llm = get_llm(model=DEFAULT_MODEL, temperature=0)
    return _llm
```

모듈 레벨에서 `_llm = get_llm(...)` 하면 Railway 시작 시 OPENAI_API_KEY 없어서 크래시.

### 대화 히스토리

`conversation.py`의 두 함수로 멀티턴 대화 지원:

```python
build_context_query(query, messages)  # 이전 대화 맥락을 현재 쿼리에 통합
make_history_section(messages)        # 보고서용 대화 히스토리 텍스트 블록
```

### Chroma 임베딩 함수

`db/chroma_search.py`의 `_get_ef()`:

```python
class _OpenAIEF(EmbeddingFunction):
    def __call__(self, input):
        client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        resp = client.embeddings.create(model="text-embedding-3-small", input=input)
        return [e.embedding for e in resp.data]
```

Chroma 내장 EF(ONNX MiniLM 384d) ≠ 빌드 시 사용 모델(OpenAI 1536d)이므로  
모든 `get_collection()` 호출에 반드시 `embedding_function=_get_ef()` 전달 필요.

### DEFAULT_MODEL

`utils/llm.py`:
```python
DEFAULT_MODEL = "gpt-4.1"
```

---

## 에이전트별 비교표

| 에이전트 | 프레임워크 | 데이터 소스 수 | LLM 호출 수 | 응답 시간 (평균) |
|---------|-----------|--------------|------------|----------------|
| MULTI | LangGraph | 7 | 2~3회 | 30~60초 |
| INSIGHT | LangGraph | 3 | 3~5회 (retry 포함) | 20~40초 |
| TAXLAW_PREC | Tool Use | 1 | 2~3회 | 10~20초 |
| TAXTR | Tool Use | 1 | 2~3회 | 10~20초 |
| STRATEGY | LangGraph | 3 | 2회 | 15~30초 |
| REBUTTAL | LangGraph | 3 | 3~4회 (reflect 포함) | 20~40초 |
| TREND | LangGraph | 2 | 2회 | 15~25초 |
| ITCL | LangGraph | 3 | 2회 | 15~30초 |
| RISK (소송) | LangGraph | 3 | 2회 | 15~25초 |
| LAW_RISK (개정) | 단순 파이프라인 | 1 (JSON DB) | 2회 | 15~30초 |
