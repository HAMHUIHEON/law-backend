# Lapis Nexus

> 판례를 요약하는 AI가 아닌, 법원의 **논증 구조**를 분석하는 AI

한국 세법 판결문·조세심판 재결례·세법 조문을 벡터 검색으로 연결하고, LangGraph 멀티 에이전트로 실무 보고서를 자동 생성합니다.

**→ [https://lapis.nexus](https://lapis.nexus)** 에서 바로 사용할 수 있습니다.

---

## 왜 만들었나

국세청에서 7년간 세무 검토·조사를 담당했습니다.

판례를 다루다 보면 두 가지 작업이 완전히 다르다는 걸 체감합니다.

- **"이 판결이 무엇에 관한 것인가"** → 요약으로 해결됨
- **"이 판결은 왜 이 결론에 도달했는가"** → 논증 구조를 읽어야 함

기존 법률 AI는 전자에 집중합니다. 실무에서 실제로 필요한 건 후자입니다.  
Lapis Nexus는 그 간극을 메우기 위해 만들었습니다.

---

## 핵심 기능

### 10개 AI 에이전트

| 에이전트 | 설명 | 주요 데이터 소스 |
|---------|------|----------------|
| **종합 리서치** | 7개 소스 병렬 검색 → 통합 보고서 | Neo4j + Chroma 4종 + 쟁점 캐시 + PDF |
| **판례 심층 분석** | 사건번호 기반 논증 구조 분석 → 전략 인사이트 | Neo4j 그래프 + 세법 조문 |
| **법원 판례 검색** | 자연어 질의 → 유사 판례 6건 분석 + 실무 시사점 | NTS 법원 판례 32,628건 |
| **조세심판 재결례** | 심판원 재결례 검색 + 인용/기각 패턴 분석 | 조세심판 재결례 2,463건 |
| **불복전략 분석** | 처분일 입력 → 불복 기한 자동 계산 + 전략 우선순위 | 법원 판례 + 재결례 + 세법 조문 |
| **반론 초안 작성** | 과세처분 이유서 입력 → 심판청구서/이의신청서 초안 | 납세자 승소 판례 + 인용 재결례 |
| **판례 트렌드** | 세목·쟁점별 연도별 승소율 통계 + 트렌드 분석 | 법원 판례 연도별 집계 |
| **국제조세 분석** | OECD 가이드라인 기반 이전가격·APA 분석 | Neo4j 국제조세 + 세법 조문 |
| **개정법령 리스크** | 법령 개정이 기존 판례에 미치는 소송 리스크 분석 | 법원 판례 + 재결례 + 세법 조문 |
| **법령개정 분석** | "법인세법 최근 개정 내용은?" → 조문별 실무 리스크 정리 | 법령정보센터 14개 세법 최신 개정문 |

### 판결문 분석 파이프라인

판결문 PDF → 10단계 구조화 파이프라인으로 논증 구조를 추출합니다.

```
문단 구조 복원 (논리 단위 분리)
    ↓
문장 역할 분류 (사실인정 / 법리 / 판단 / 결론)
    ↓
논증 블록 생성 (premise → evidence → rule → inference → conclusion)
    ↓
쟁점별 구조 분석 (issue framing + issue logic)
    ↓
법령·판례 인용 연결 (Neo4j 그래프)
    ↓
3종 보고서 생성 (흐름 요약 / 구조 브리핑 / 실무 인사이트)
```

---

## 데모 결과

실제 Railway API 호출 결과입니다. [`docs/demo/`](docs/demo/) 폴더에서 각 에이전트 전체 출력을 확인할 수 있습니다.

| 에이전트 | 테스트 쿼리 | 응답 시간 |
|---------|-----------|---------|
| 종합 리서치 | 이전가격 과소신고 관련 판례와 세법 조문을 종합 분석해줘 | 13.3s |
| 판례 심층 분석 | 부당행위계산부인 적용 기준에 관한 판례 전략 보고서를 작성해줘 | 24.0s |
| 법원 판례 검색 | 명의신탁 증여세 과세처분 관련 법원 판례를 찾아줘 | 9.9s |
| 조세심판 재결례 | 경비 부인 처분에 대한 조세심판 재결례를 분석해줘 | 20.0s |
| 불복전략 분석 | 이전가격 과세처분을 받았습니다. 불복 전략을 분석해줘 (처분일: 2025-10-01, 세액: 5억원) | — |
| 반론 초안 작성 | 로열티 이전가격 TNMM 15억원 초과분 손금불산입 처분 → 심판청구서 작성 | 30.0s |
| 판례 트렌드 | 최근 5년간 부가세 매입세액 공제 거부 판례 트렌드를 분석해줘 | 13.2s |
| 국제조세 분석 | GLOBE 필라2 세액공제 관련 국제조세 판례와 법령을 분석해줘 | 14.6s |
| 개정법령 리스크 | 조세특례제한법 R&D 세액공제 요건 강화 개정에 따른 소송 리스크 | 12.7s |
| 법령개정 분석 | 법인세법이 최근에 어떻게 바뀌었나요? | 10.4s |

---

## 데이터 현황

### 벡터 DB (Railway Volume, ChromaDB)

| 컬렉션 | 건수 | 소스 |
|--------|------|------|
| `taxlaw_prec` | 32,628건 | NTS taxlaw.nts.go.kr 법원 판례 |
| `inquiry_cases` | 119,427건 | 국세청 질의회신 |
| `law_articles` | 6,660건 | 법령정보센터 14개 세법 조문 (법+령+규칙) |
| `taxtr_cases` | 2,463건 | 조세심판원 재결례 |
| `pdf_court_cases` | 560건 | 구조화 판결문 (narrative + issue_logic) |

### 그래프 DB (Neo4j AuraDB)

- **Norm 레이어**: 법·령·규칙 조문 계층 구조 (Law → Chapter → Article → Paragraph)
- **Integrated 레이어**: ITCL 65개 버전 통합 의미 분석 (SemanticIssue / ReasoningIssue)
- **판례 네트워크**: 인용 관계 + 법령 참조 연결

### 세법 조문 DB (14개 세법)

법인세법, 소득세법, 부가가치세법, 국세기본법, 국세징수법, 국제조세조정법, 상속세및증여세법, 조세특례제한법, 관세법, 자본시장법, 개별소비세법, 종합부동산세법, 조세범처벌법, 조세범처벌절차법

---

## 아키텍처

### 에이전트 시스템 (LangGraph StateGraph)

```
사용자 질의
    ↓
[Supervisor] 쿼리 분해 + 소스별 특화 쿼리 생성
    ↓
[병렬 검색 노드]
  ├── Neo4j 벡터 검색 (국제조세 판례)
  ├── Chroma law_articles (세법 조문 6,660건)
  ├── Chroma taxlaw_prec (법원 판례 32,628건)
  ├── Chroma taxtr_cases (조세심판 2,463건)
  ├── 쟁점 캐시 (구조화 판례 쟁점 벡터 1,021건)
  ├── Chroma pdf_court_cases (PDF 판결문 560건)
  └── Chroma inquiry_cases (질의회신 119,427건)
    ↓
[Synthesizer] GPT-4.1 통합 보고서 생성
```

### Citation Guard (반론 초안)

반론 초안의 판례 번호 환각을 방지합니다.

```python
# 생성된 텍스트에서 판례번호 regex 추출
→ 실제 검색 결과 교차 검증
→ 미검증 번호는 [검증필요: ...] 표시 후 unverified_citations 반환
```

### Lazy Init 패턴

Railway cold-start 시 모든 에이전트·DB 클라이언트가 지연 초기화됩니다.

```python
_agent: Agent | None = None
def _get_agent() -> Agent:
    global _agent
    if _agent is None:
        _agent = Agent()
    return _agent
```

---

## 기술 스택

| 레이어 | 기술 |
|--------|------|
| Backend | FastAPI + Python 3.11 |
| AI Pipeline | LangChain + LangGraph (StateGraph) |
| LLM | OpenAI GPT-4.1 |
| Graph DB | Neo4j AuraDB |
| Vector DB | ChromaDB (Railway Volume) |
| Embeddings | OpenAI text-embedding-3-small (1536d) |
| Auth | Clerk (JWT) |
| Frontend | Next.js + TypeScript |
| Deployment | Vercel (frontend) + Railway (backend) |

---

## 로컬 실행

### 사전 준비

- Python 3.11+, Node.js 18+
- OpenAI API 키
- Neo4j 인스턴스 (로컬 또는 AuraDB)
- ChromaDB 로컬 빌드 (`scripts/build_law_vector_db.py`)

### 백엔드

```bash
cd backend

# 환경변수 설정 (29_FINAL/.env)
# OPENAI_API_KEY, NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD, CLERK_ISSUER, DEV_MODE=true

pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

### 프론트엔드

```bash
cd law-frontend

# .env.local
# NEXT_PUBLIC_API_BASE=http://localhost:8000
# NEXT_PUBLIC_DEV_MODE=true
# NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=<Clerk 테스트 키>

npm install && npm run dev
```

### API 테스트 (`DEV_MODE=true` 시 인증 불필요)

```bash
# 종합 리서치
curl -X POST http://localhost:8000/api/agent/multi \
  -H "Content-Type: application/json" \
  -d '{"query": "이전가격 과세처분 판례 주요 판단 기준"}'

# 법원 판례 검색
curl -X POST http://localhost:8000/api/prec/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "명의신탁 증여세 과세 관련 판례"}'

# 법령개정 분석
curl -X POST http://localhost:8000/api/risk/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "법인세법이 최근에 어떻게 바뀌었나요?"}'

# 불복전략
curl -X POST http://localhost:8000/api/strategy/strategy \
  -H "Content-Type: application/json" \
  -d '{"summary": "이전가격 과세처분", "disposition_date": "2025-10-01", "tax_amount": "5억원"}'
```

---

## 프로젝트 구조

```
29_FINAL/
├── backend/
│   ├── agents/          # LangGraph 에이전트 (multi, insight, taxlaw_prec, taxtr, strategy, rebuttal, trend, itcl, risk)
│   ├── RISK/            # 법령개정 분석 모듈 (consulting, agent, monitor)
│   ├── db/              # Neo4j 클라이언트 + ChromaDB 검색 유틸리티
│   ├── routers/         # FastAPI 엔드포인트
│   ├── utils/           # Citation Guard + 캐시 관리
│   ├── init_chroma.py   # Railway cold-start: Chroma Volume 다운로드
│   └── init_law.py      # Railway cold-start: 세법 JSON DB 다운로드
├── docs/
│   ├── agent_architecture.md  # 에이전트 상세 아키텍처 문서
│   ├── demo/                  # 10개 에이전트 실제 실행 결과 (MD)
│   └── demo_raw/              # API 응답 원본 JSON
├── law-frontend/        # Next.js 프론트엔드
│   └── app/agent/       # 에이전트 전용 UI (Google 검색 스타일)
└── scripts/             # 데이터 빌드 스크립트
```

---

## 문서

- [`docs/agent_architecture.md`](docs/agent_architecture.md) — 10개 에이전트 전체 아키텍처 상세 문서
- [`docs/demo/`](docs/demo/) — 에이전트별 실제 실행 결과
- [`CLAUDE.md`](CLAUDE.md) — 개발 가이드 (배포 현황, Chroma DB 상태, 환경변수)

---

## 개발 히스토리

| 버전 | 핵심 변화 |
|------|-----------|
| v1 | RAG 기반 판례 검색 |
| v2 | 문단 구조 복원 추가 |
| v3 | 10단계 파이프라인 설계 |
| v4 | Neo4j 그래프 DB 도입 |
| v5 | 3종 보고서 + 실무 인사이트 |
| v6 | LangGraph 기반 AI 에이전트 시스템 (4종) |
| v7 (현재) | 10종 에이전트 + ChromaDB Railway Volume (163,738건) + 법령개정 분석 |

---

## 만든 사람

국세청 경력 7년 → Legal AI Product Builder

세무조사 현장에서 판례와 법령이 실제로 어떻게 작동하는지 경험했습니다.  
그 경험을 바탕으로 법률가가 실제로 필요로 하는 AI 시스템을 직접 설계하고 구현하고 있습니다.

- Email: seungmiyoon0723@gmail.com
