# Lapis Nexus — 에이전트 품질 평가 보고서

> 작성일: 2026-06-14  
> 평가 대상: 9종 에이전트 (`multi_agent`, `insight_agent`, `rebuttal_agent`, `strategy_agent`, `trend_agent`, `itcl_agent`, `risk_agent`, `taxlaw_prec_agent`, `taxtr_agent`)  
> 평가 기준: 세법 전문 실무자(세무사·조세전문변호사) 직접 사용 가능 수준  
> 참고: LBox 히카리 설계 철학(vector-db-overview, ai-legal-stack, hybrid retrieval)

---

## 종합 평가 요약

| 에이전트 | 현재 수준 | 실무 사용 가능성 | 가장 큰 문제 |
|---------|----------|----------------|------------|
| SupervisorAgent (MULTI) | ★★★☆☆ | 리서치 보조 수준 | 6소스 독립 병렬 검색, 교차 검증 없음 |
| InsightAgent | ★★★★☆ | 가장 완성도 높음 | Critic 실질 기능 없음, case_id 의존 |
| RebuttalAgent | ★★☆☆☆ | 초안 뼈대만 | 법적 문서 형식 미준수, 인용 검증 없음 |
| StrategyAgent | ★★★☆☆ | 방향 제시 수준 | 절차 기한 없음, 승산 확률 없음 |
| TrendAgent | ★★☆☆☆ | 통계 제공 수준 | 법리 변천사 전체 LLM 생성 |
| ITCLAgent | ★★★☆☆ | 방법론 체크용 | OECD 지침 DB 없음, 5개 방법 전부 출력 |
| RiskAgent | ★★☆☆☆ | 위험 인식 수준 | 개정 조문 인용 추적 없음 |
| TaxlawPrecAgent | ★★★☆☆ | 판례 검색 도우미 | 단순 벡터 검색 + LLM 요약만 |
| TaxtrAgent | ★★★☆☆ | 재결례 검색 도우미 | 단순 벡터 검색 + LLM 요약만 |

**한 줄 진단**: 현재 에이전트들은 "검색 + 요약" 수준. 실무 세무사가 보조 도구로 쓰기엔 충분하지만, 독립 판단 자료로 신뢰하기엔 얕다. 핵심 문제는 ①리트리버 구조, ②법령 시점 관리, ③출력 검증 로직 세 가지다.

---

## 1. 리트리버 구조 — 근본적 약점

### 1.1 현재 구조

모든 에이전트가 동일한 패턴을 쓴다:

```
쿼리 → text-embedding-3-small 임베딩 → Chroma 코사인 유사도 → top-K 반환
```

이것만으로는 실무 수준에 미달하는 이유:

**① 키워드 정확 매칭 없음**

"국세기본법 제35조 제1항"이라는 조문을 물어봤을 때, 벡터 검색은 의미 유사도로 찾기 때문에 해당 조문이 최상위에 오지 않을 수 있다. 법률 검색에서 조문 번호 정확 매칭은 필수다. LBox가 Elasticsearch(BM25 키워드) + Milvus(벡터)를 병렬 운용하는 이유가 정확히 이것이다.

**② 한국어 법률 텍스트 최적화 부재**

`text-embedding-3-small`은 영어 우선 모델이다. 한국어 법률 텍스트에서는 `BGE-m3`, `intfloat/multilingual-e5-large`, `KURE`(Korean legal embedding) 계열이 유의미하게 더 좋은 recall을 보인다. LBox 내부 평가 기준인 **Citation Recall**(모범답안에 있는 판례번호가 검색 결과에 들어오는 비율)에서 현재 임베딩은 상당한 손실이 발생한다.

**③ 리랭킹 없음**

top-K 검색 후 Cross-Encoder 리랭킹이 없으면, 코사인 유사도 순서와 실제 질의 관련성 순서가 다를 수 있다. 특히 세법처럼 같은 단어가 다른 문맥에서 쓰이는 도메인에서 이 격차가 크다.

### 1.2 권고 개선안

```
현재: 쿼리 → [벡터 검색] → LLM

개선안:
쿼리 → [BM25 키워드 검색] ─┐
       → [벡터 검색]       ─┤ → RRF(Reciprocal Rank Fusion) → Cross-Encoder 리랭킹 → LLM
       → [조문번호 정규식]  ─┘
```

**구현 우선순위:**
1. Chroma에 `.get()` API로 exact match fallback 추가 (조문 번호 `제X조` 패턴 감지 시 정규식 검색 병행) — 즉시 구현 가능
2. BGE-m3로 임베딩 모델 교체 후 Chroma 재빌드 — 중기
3. BM25 레이어 추가 (langchain `BM25Retriever` 또는 Elasticsearch) — 장기

---

## 2. 법령 시점 관리 — 완전히 빠진 기능

### 2.1 현재 문제

`law_articles` Chroma 컬렉션은 정적 스냅샷이다. 메타데이터에 법령 버전/공포일이 있더라도, 에이전트가 "이 거래가 2022년에 발생했을 때 적용되는 조문은?"이라는 질문에 답하지 못한다.

세법에서 법령 시점은 결정적이다:
- 소득세법 부당행위계산 부인 시가 기준: 2021년 개정으로 ±5%에서 ±3%로 변경
- 국조법 정상가격 산출방법 우선순위: 2019년 개정
- 가산세율, 부과제척기간: 개정 이력이 결론을 바꿈

현재 에이전트가 과거 거래에 대해 현행 조문을 인용하면 **법적으로 틀린 답변**이 된다.

### 2.2 권고 개선안

**단기 (메타데이터 추가):**
```python
# law_articles 메타데이터에 추가할 필드
{
    "effective_date": "2021-01-01",   # 시행일
    "abolition_date": "2023-12-31",   # 폐지일 (없으면 null)
    "version_key": "20210101_12345",  # 공포번호
}
```

Chroma의 `where` 파라미터로 날짜 범위 필터 적용:
```python
collection.query(
    query_embeddings=[...],
    where={
        "$and": [
            {"effective_date": {"$lte": transaction_date}},
            {"$or": [
                {"abolition_date": {"$gte": transaction_date}},
                {"abolition_date": None}
            ]}
        ]
    }
)
```

**에이전트 입력 확장:**
- `StrategyAgent`, `ITCLAgent`, `RebuttalAgent`에 `transaction_date` 파라미터 추가
- 시점 정보 없으면 "현행 기준" 명시하고 경고 출력

---

## 3. 에이전트별 상세 평가

---

### 3.1 SupervisorAgent (MULTI) ★★★☆☆

**잘 된 것:**
- 6개 소스 병렬 탐색 구조는 올바른 방향
- ITCL 키워드 감지 후 전용 쿼리 분기 — 실용적
- synthesizer 프롬프트의 섹션 구조(핵심요약→법령→판례→전략→리스크→체크리스트) 좋음

**문제점:**

1. **소스 간 교차 검증 없음**: 6개 소스에서 동일 판례가 다른 결론으로 나올 수 있는데 합산만 한다. 예를 들어 Neo4j에는 "납세자 패" 로 저장되고 taxlaw_prec에는 같은 사건이 다른 심급 결과로 저장될 수 있다.

2. **결과 중복 제거 없음**: Neo4j와 taxlaw_prec, pdf_court_cases에 동일 판례가 중복 검색되어도 synthesizer에 모두 전달된다. 컨텍스트 낭비.

3. **소스 품질 불균형 무시**: Neo4j는 270건 구조화, taxlaw_prec은 32,628건 비구조화. 이 둘을 동일 가중치로 합산하는 것은 비효율.

4. **정렬/랭킹 없음**: 6개 소스 결과를 concatenate하는데, 유사도 기준으로 교차 정렬한 것이 아니다.

**개선 방향:**
```python
# 현재: 6개 독립 검색 → 무조건 합산
# 개선: 결과 통합 후 dedup + rerank

def _fuse_results(sources: dict) -> list:
    """RRF(Reciprocal Rank Fusion) 기반 결과 통합"""
    scores = {}
    for source_name, results in sources.items():
        for rank, r in enumerate(results):
            key = r.get("case_no") or r.get("case_id") or r.get("doc_id")
            if key:
                scores[key] = scores.get(key, 0) + 1 / (rank + 60)
    return sorted(scores.items(), key=lambda x: -x[1])
```

---

### 3.2 InsightAgent ★★★★☆ (가장 완성도 높음)

**잘 된 것:**
- Planner → Executor → Insight → Critic → Reporter 5단계 구조가 실제 법률 리서치 사고 흐름과 맞음
- Critic 단계가 있다는 것 자체가 다른 에이전트보다 우위
- `case_id` 제공 시 ExportC deep insight 연동 — 강력한 기능

**문제점:**

1. **Critic이 사실상 무기능**: `search_results == 0`일 때만 retry한다. 결과가 5건 나와도 모두 무관한 판례일 수 있는데 이를 감지하지 못한다.

```python
# 현재 critic
should_retry = (search_count == 0) and (state["retry_count"] < _MAX_RETRIES)

# 개선안: 유사도 임계값 + 관련성 LLM 판단
should_retry = (
    search_count == 0 or 
    max_similarity < 0.7 or  # 최고 유사도가 낮을 때
    not _is_relevant(results, state["query"])  # LLM 관련성 판단
)
```

2. **ExportC insight_node가 실제로 거의 안 쓰임**: 프론트에서 case_id를 직접 입력하는 UX가 없어서 `insight_result`는 대부분 None이다.

3. **Neo4j 연결을 Executor마다 새로 생성**: retry 시 두 번 연결/해제.

---

### 3.3 RebuttalAgent ★★☆☆☆ (가장 시급한 개선 필요)

**문제점 (실무 사용 불가 수준):**

**① 법적 문서 형식 완전히 틀림**

현재 출력 구조:
```
## 처분의 위법성 개요
## 쟁점별 반론
## 관련 판례 및 재결례 요지
## 관련 법령 근거
## 결론 및 청구취지
```

실제 이의신청서/심판청구서 형식:
```
청구 번호:
청구인: [납세자명/주민등록번호]
처분청: [세무서명]

1. 처분의 내용
   과세표준: OOO원
   세액: OOO원
   처분일: YYYY.MM.DD

2. 청구의 취지
   "위 처분은 취소되어야 한다"

3. 이유
   가. 처분 경위
   나. 법령의 적용
      국세기본법 제XX조 제X항 (인용 조문 원문)
   다. 유사 판례
      대법원 20XX두XXXX 판결 (YYYY.MM.DD 선고)
      판시사항: "..."
      이 사건과의 관련성: ...
   라. 결론
```

에이전트가 청구인 정보, 처분청, 처분 날짜, 세액 등 **기본 요소를 입력받지 않는다**.

**② 판례 인용 형식 오류**

법적 문서에서 판례는 반드시 `대법원 2020두12345 판결`처럼 정확한 번호가 있어야 한다. 현재 에이전트는 Chroma에서 검색된 판례의 `case_no` 필드를 LLM에 전달하지만, LLM이 이것을 정확히 인용할 보장이 없다.

**③ Reflector가 형식 검증을 안 함**

```python
# 현재 Reflector 평가 기준
"1. 과세관청 주장을 정확히 반박하는가"  # 너무 추상적
"2. 판례·재결례 인용이 구체적이고 적절한가"  # 검증 불가
```

**개선 방향 (우선순위 1)**:

```python
# 1단계: 입력 확장
class RebuttalInput:
    disposition_text: str
    taxpayer_name: str
    taxpayer_id: str
    tax_office: str
    disposition_date: str  # "YYYY.MM.DD"
    tax_amount: int
    tax_type: str  # "법인세", "소득세" 등
    filing_type: str  # "이의신청" | "심판청구" | "행정소송"

# 2단계: 판례 번호 검증 노드 추가
def citation_verifier_node(state):
    """LLM이 생성한 반론 초안에서 판례번호 추출 후 Chroma에서 실제 존재 확인"""
    import re
    cited = re.findall(r'\d{4}[가-힣]+\d+', state["draft"])
    retrieved_cases = {r.get("case_no") for r in state["winning_court_cases"]}
    unverified = [c for c in cited if c not in retrieved_cases]
    if unverified:
        # 미검증 인용 제거 또는 경고 추가
        pass

# 3단계: 법적 문서 템플릿 강제
FILING_TEMPLATE = """
이 의 신 청 서

청구인: {taxpayer_name} ({taxpayer_id})
처분청: {tax_office}

1. 처분의 내용
...
"""
```

---

### 3.4 StrategyAgent ★★★☆☆

**잘 된 것:**
- FactExtractor → CaseSearcher → Strategist 흐름이 실무 상담 절차와 유사
- 경정청구/심판청구/행정소송 3가지 경로를 모두 검토하는 구조

**문제점:**

1. **절차 기한이 없음**: 세법에서 가장 중요한 것 중 하나가 기한이다.
   - 경정청구: 법정신고 기한으로부터 5년 (국세기본법 제45조의2)
   - 이의신청: 처분 통지받은 날로부터 90일
   - 심판청구: 처분 통지받은 날로부터 90일
   - 행정소송: 전치주의 (심판청구 먼저 해야 소 제기 가능)
   
   에이전트가 처분일/신고일 없이 전략을 논의하면 오히려 해롭다 — 기한 놓친 전략 권고 가능.

2. **승산 확률 수치 없음**: "유사 판례 8건 중 납세자 승 3건 (37.5%)" 같은 수치 없이 "가능성이 있다"만 서술.

3. **사실관계 유사성 분석이 피상적**: FactExtractor가 추출한 key_facts를 판례와 비교할 때 사실관계의 어느 부분이 일치/불일치하는지 분석 안 함.

**개선 방향:**
```python
# 입력에 날짜 추가
class StrategyInput:
    client_summary: str
    disposition_date: str    # "YYYY-MM-DD"
    tax_amount: int
    already_filed: bool      # 이미 이의신청 했는지

# Strategist 프롬프트에 기한 계산 추가
def _compute_deadlines(disposition_date: str) -> dict:
    from datetime import datetime, timedelta
    base = datetime.strptime(disposition_date, "%Y-%m-%d")
    return {
        "이의신청마감": (base + timedelta(days=90)).strftime("%Y-%m-%d"),
        "심판청구마감": (base + timedelta(days=90)).strftime("%Y-%m-%d"),
        # 경정청구는 사안마다 다름
    }
```

---

### 3.5 TrendAgent ★★☆☆☆

**핵심 문제: 법리 변천사 전체가 LLM 생성**

현재 에이전트는 다음만 갖는다:
- 연도별 케이스 수 + 납세자 승소 수 (통계)
- 판례 샘플 제목 5건

이것으로 LLM에게 "법리 변천사를 서술하라"고 시키면, LLM은 학습 데이터에서 알고 있는 것을 서술하고 데이터를 증거로 쓸 수 없다. **즉 법리 변천사 섹션은 환각 위험이 가장 높은 부분이다.**

실제로 필요한 것:
```
2018년: 대법원 2015두1243 → "부당행위계산 시가 산정 기준 처음 명시"
2020년: 대법원 2019두5678 → "5년 이내 거래가 있는 경우 그 가격을 시가로 추정"
2023년: 대법원 2021두9012 → "매매사례가액 우선 적용 요건 완화"
```

이런 구체적 법리 변화를 감지하려면 **판례 원문 검색 후 핵심 판시사항 추출**이 필요하다.

**개선 방향:**
```python
# DataCollector에 대표 판례 원문 추가
def data_collector_node(state):
    # 기존: 통계만
    stats = get_taxlaw_prec_stats(...)
    
    # 추가: 연도별 대표 판례 원문 (상위 2건씩)
    landmark_cases = {}
    for year in sorted(stats["year_stats"].keys())[-10:]:
        cases = search_taxlaw_prec_by_year(state["query"], year, n=2)
        if cases:
            landmark_cases[year] = [
                {"case_no": c["case_no"], "decision": c["decision"], 
                 "text_excerpt": c.get("document", "")[:300]}
                for c in cases
            ]
    
    return {"trend_data": stats, "landmark_cases": landmark_cases, ...}
```

---

### 3.6 ITCLAgent ★★★☆☆

**잘 된 것:**
- ITCL 전문 시스템 프롬프트 (CUP/RPM/COST+/TNMM/PSM 명시)
- Neo4j ITCL그래프와 Chroma 이중 검색

**문제점:**

1. **5가지 방법 모두 출력**: 실제 이전가격 보고서는 해당 거래에 적합한 방법 1~2개를 선택하고 나머지는 왜 부적합한지 설명한다. 에이전트는 항상 5개 방법을 모두 나열 — 실무에서는 오히려 혼란.

2. **OECD 이전가격 지침 없음**: 국내 판례와 국조법만으로는 부족하다. OECD TPG(Transfer Pricing Guidelines) 2022판의 챕터별 기준이 KB에 없다.

3. **비교가능 회사/거래 데이터 없음**: TNMM 적용 시 비교가능 회사의 순이익률 범위(IQR)를 실제로 계산해야 하는데, 이 데이터가 없다.

4. **이중과세 방지 조약(DTT) 분석 없음**: 국외특수관계인이 있는 국가의 조세조약 규정이 중요한데 이 레이어가 없다.

**개선 방향 (현실적 단기):**
```python
# 거래 구조 입력 강화
class ITCLInput:
    query: str
    transaction_type: str  # "유형자산 매각" | "무형자산 라이선스" | "용역 제공" | "금전 대여"
    related_party_country: str  # 상대방 국가
    transaction_amount: int
    transaction_year: str
    
# 거래 유형에 따른 우선 방법 사전 결정
PREFERRED_METHODS = {
    "유형자산 매각": ["CUP", "TNMM"],
    "무형자산 라이선스": ["CUP", "PSM"],
    "용역 제공": ["COST+", "TNMM"],
    "금전 대여": ["CUP"],  # 정상이자율
}
```

---

### 3.7 RiskAgent ★★☆☆☆

**핵심 문제: 시행일 기반 케이스 필터링 없음**

현재 리스크 분류(🔴🟡🟢)는 "법령명 + 개정 내용"으로 벡터 검색된 판례를 LLM이 임의로 분류한다. 이 방법의 문제:

1. **시행일 이전 판례도 포함**: 개정 전에 선고된 판례는 이미 다른 법 기준으로 판결된 것이다. 이것이 🔴로 분류되어도 의미가 없다.

2. **인용 관계 없이 유사도만**: 실제로 해당 조문을 인용한 판례와 단지 관련 주제인 판례를 구분하지 못한다.

3. **개정 조문 원문 비교 없음**: 개정 전후 조문 텍스트를 나란히 놓고 "무엇이 바뀌었는가"를 정확히 알아야 영향 판례를 정확히 찾을 수 있다.

**개선 방향:**
```python
def case_finder_node(state):
    # 기존: 법령명 + 개정내용으로 벡터 검색
    
    # 개선 1: 조문 번호 정규식으로 정확 매칭
    import re
    article_refs = re.findall(r'제(\d+)조', state["revision_summary"])
    
    # 개선 2: 시행일 기준 판례 분리
    pre_revision_cases = search_by_date_range(
        query=statute_query, 
        end_date=state["effective_date"]
    )
    post_revision_cases = search_by_date_range(
        query=statute_query,
        start_date=state["effective_date"]
    )
    
    return {
        "pre_revision_cases": pre_revision_cases,  # 개정 전 판례 (이제 기준이 달라짐)
        "post_revision_cases": post_revision_cases,  # 개정 후 판례 (새 기준 적용)
        ...
    }
```

---

## 4. 히카리 설계 철학에서 배울 것

히카리 LBox 에이전트 설계에서 Lapis Nexus에 즉시 적용 가능한 것:

### 4.1 "환각 0% 가능한 인용 패턴" (ai-legal-stack/rag)

히카리 메모에서 `Citation Recall`을 핵심 지표로 쓰는 이유: LLM이 판례 번호를 생성할 때 검색된 데이터에 없는 번호를 만들어낼 수 있다. 현재 에이전트에는 이것을 막는 구조적 장치가 없다.

**즉시 적용 가능한 Citation Guard:**
```python
def citation_guard(report: str, retrieved_cases: list) -> str:
    """생성된 보고서에서 판례 번호를 추출하고 검색 결과에 없으면 제거."""
    import re
    # 대법원 2020두12345, 조심 2022서1234 등
    cited = re.findall(r'(대법원|서울고법|조심|국심)\s*\d{4}[가-힣]+\d+', report)
    valid = {r.get("case_no") or r.get("dem_no", "") for r in retrieved_cases}
    
    for c in cited:
        if c not in valid:
            report = report.replace(c, f"[검증필요: {c}]")
    return report
```

### 4.2 World Model 기반 쿼리 확장

히카리의 `domain-world-model`에서 핵심 개념은 "같은 질문을 두 번 받지 않으려면 답변을 한 곳에 모아야 한다"는 것이다. Lapis Nexus 에이전트에 적용하면:

- 세무사가 자주 묻는 쟁점 패턴 → **쿼리 확장 사전** 구축
  - "이전가격" → ["정상가격", "독립기업원칙", "TNMM", "국외특수관계인", "국조법 제4조~10조"]
  - "부당행위계산" → ["시가", "법인세법 제52조", "특수관계인", "저가양도"]
- 이 사전을 쿼리 전처리에 사용하면 벡터 검색 recall이 크게 개선됨

### 4.3 Friction-Log 기반 에이전트 자기개선

히카리의 자기진화 메커니즘을 Lapis Nexus에 적용하면:
- 세무사가 "이 판례는 왜 안 나왔어?"라는 피드백 → 어떤 쿼리 패턴에서 retrieval miss가 났는지 기록
- 5건 이상 같은 패턴 miss → 해당 쿼리에 특화된 fallback 쿼리 추가

---

## 5. 우선순위별 개선 로드맵

### Phase 1 — 즉시 가능 (1~2주)

| 항목 | 작업 | 기대 효과 |
|------|------|----------|
| Citation Guard | 보고서에서 판례번호 추출 → Chroma 검증 → 미검증 표시 | 환각 판례 번호 방어 |
| RebuttalAgent 입력 확장 | `taxpayer_name`, `disposition_date`, `tax_type`, `filing_type` 파라미터 추가 | 실제 서류 생성 가능 |
| StrategyAgent 기한 계산 | `disposition_date` 입력 → 90일/5년 기한 자동 계산 | 가장 실용적인 단기 개선 |
| ITCL 거래 유형 입력 | `transaction_type` 으로 권장 방법 사전 결정 | 5개 방법 전부 나열 문제 해결 |
| 조문번호 정규식 fallback | `제X조` 패턴 감지 시 Chroma `.get()` 정확 매칭 병행 | 조문 검색 정확도 향상 |

### Phase 2 — 중기 (1~2개월)

| 항목 | 작업 | 기대 효과 |
|------|------|----------|
| BGE-m3 임베딩 교체 | 전체 Chroma 컬렉션 재빌드 | 한국어 법률 recall 20~30% 향상 |
| law_articles 시점 메타데이터 | `effective_date`, `abolition_date` 추가 + 쿼리 시 date filter | 시점별 법령 조회 가능 |
| Cross-Encoder 리랭킹 | `cross-encoder/ms-marco-MiniLM-L-6-v2` 또는 Cohere Rerank | top-K 정확도 향상 |
| InsightAgent Critic 강화 | 유사도 임계값 + LLM 관련성 판단 | 무관한 판례 포함 방어 |

### Phase 3 — 장기 (3~6개월)

| 항목 | 작업 | 기대 효과 |
|------|------|----------|
| BM25 레이어 추가 | Elasticsearch 또는 `rank_bm25` 라이브러리 | 키워드 정확 검색 |
| TrendAgent 랜드마크 판례 | 연도별 대표 판례 원문 추출 → 법리 변천 분석 | 법리 변천사 환각 제거 |
| OECD TPG 문서 KB 추가 | PDF 파싱 → 별도 Chroma 컬렉션 | ITCL 에이전트 국제 기준 강화 |
| Multi-agent Citation Recall 평가 | 테스트셋 50~100건으로 recall@5, recall@10 측정 | 개선 효과 정량화 |

---

## 6. 세무사가 실제로 쓸 수 있는 수준까지의 갭

현재 에이전트들이 "세무사가 직접 쓸 수 있는 수준"에 도달하려면 각 에이전트에서 부족한 것:

| 에이전트 | 지금 쓸 수 있는 용도 | 직접 사용 불가한 이유 |
|---------|------------------|-------------------|
| MULTI | 세법 리서치 시작점 파악 | 판례 번호 검증 안 됨, 소스 간 충돌 미처리 |
| InsightAgent | 특정 쟁점 빠른 판례 파악 | case_id 제공 안 하면 deep insight 없음 |
| RebuttalAgent | **현재 직접 사용 불가** | 법적 문서 형식 안 맞음, 청구인 정보 없음 |
| StrategyAgent | 전략 방향 참고 | 기한 없음, 승산 수치 없음 |
| TrendAgent | 큰 그림 확인 | 법리 변천사 환각 위험 |
| ITCLAgent | 방법론 체크리스트 | OECD 기준 없음, 비교가능성 분석 없음 |
| RiskAgent | 개정 인식 도우미 | 조문 인용 추적 없음 |

**결론**: 현재 가장 실용적인 에이전트는 `InsightAgent`와 `TaxlawPrecAgent`/`TaxtrAgent`. `RebuttalAgent`는 현재 구조로는 법적 문서 생성에 부적합 — Phase 1 개선이 가장 급하다.

---

## 부록: 에이전트 아키텍처 전반에 공통 적용할 패턴

### A. 단계별 출처 추적 (Provenance)

각 에이전트 출력에 "이 문장은 어느 판례에서 나왔는가"를 추적하는 구조. 현재 없음.

```python
# 출처 추적 예시
{
    "statement": "납세자는 특수관계자 거래에서 정상가격 원칙을 소명해야 한다",
    "source": "taxlaw_prec",
    "case_no": "대법원 2020두12345",
    "similarity": 0.87
}
```

### B. 전문성 레벨 구분 출력

세무사용 vs. 납세자 일반인용 두 가지 톤으로 출력 가능하도록. 현재 모두 "실무자 보고서 톤"으로 고정.

### C. 불확실성 표현

"판례 데이터 부족으로 이 부분은 확신할 수 없습니다" 같은 confidence 표현 추가. 현재 모든 에이전트가 자신감 있는 톤으로 출력 → 세무사가 잘못 신뢰할 위험.

---

> 이 보고서는 2026-06-14 기준 에이전트 코드 전체 리뷰와 히카리 LBox 설계 문서(벡터 DB 오버뷰, AI 법률 스택) 분석을 바탕으로 작성됨.
