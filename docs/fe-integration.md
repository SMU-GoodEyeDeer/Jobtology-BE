# Jobtology API 프론트엔드 통합 가이드

이 문서는 프론트엔드 개발자가 Jobtology 백엔드 API를 연동하는 방법을 안내합니다.
필드 단위 요청/응답 명세는 이 문서에 중복 기재하지 않으며, 아래 원천 문서를 기준으로 합니다.

이 Markdown은 `/api/guide`의 단일 원본이며 wheel에도 포함됩니다. 저장소 checkout과 설치된
패키지 모두 같은 문서를 제공해야 합니다.

- Swagger UI 대화형 문서: `/api/docs`
- ReDoc 열람용 문서: `/api/redoc`
- OpenAPI 기계 판독 문서: `/api/openapi.json`

세 문서는 동일한 OpenAPI 계약을 다른 형태로 보여줍니다. 구현 중 계약이 애매할 때는
`/api/openapi.json` 이 최종 기준입니다.

## 서비스 개요

Jobtology는 사용자의 프로필·역량·목표 입력을 바탕으로 역량 분석과 학습 로드맵을
제안하는 API입니다. 핵심 흐름은 (1) 사용자 입력 수집, (2) 비동기 분석 재계산,
(3) 로드맵 제안 확인, (4) 로드맵 확정·진행, (5) 대시보드 조회입니다.

현재 worktree에는 local JSON 기반 M1–M5 제품 흐름과 읽기 전용 native `/api/v2` catalog가
구현되어 있습니다. 원격 배포, Google 로그인, 원격 publication/revocation 연동, native editorial
analysis와 route planning은 아직 제공되지 않거나 의도적으로 지원하지 않습니다. 아래 흐름의 실제
필드·상태·응답 형식 최종 기준은 배포된 서버의 `/api/openapi.json`입니다.

## 기본 URL과 버전 정책

- 배포 환경의 기본 URL은 프런트엔드와 같은 origin인 `https://jobtology.yeongmin.net`입니다.
  API·문서는 모두 `/api` 아래에 있으므로 별도 API 도메인이나 CORS 설정 없이 상대 경로로 호출할 수 있습니다.
- 사용자 데이터·분석·로드맵 제품 API는 `/api/v1` 경로 아래에 있습니다.
- 명시적으로 구성한 Neo4j 원본 카탈로그 읽기는 별도 계약인 `/api/v2` 경로 아래에
  있습니다. v2는 v1의 편집 검토 코퍼스나 사용자 데이터 API를 대체하지 않습니다.
- 버전은 경로 접두사(`v1`, `v2`)로 관리되며, 하위 호환성을 깨는 변경은 새 접두사와 함께
  발표됩니다.
- 데이터 버전은 각 자원의 `version` 필드가 관리합니다. 변경 요청에는 클라이언트가
  알고 있는 버전을 함께 전송해야 합니다(낙관적 동시성 제어, 아래 참고).
- 프로세스 생존 확인은 `GET /api/v1/health/live`입니다. 데이터베이스 준비 여부를
  검사하지 않으므로 제품 기능 가용성 판단에 사용하지 마세요.

## Neo4j 원본 카탈로그 (v2)

`JOBTOLOGY_CORPUS_SOURCE=neo4j_query_api`가 서버에 명시적으로 구성되고 원본 읽기가
가능할 때만 아래 읽기 전용 API를 사용하세요. 구성되지 않았거나 원본 연결·검증에 실패하면
표준 `503 DATA_UNAVAILABLE`를 반환합니다. 이 라우트도 인증된 세션을 요구합니다.

| 엔드포인트 | 안전한 응답 범위 |
|---|---|
| `GET /api/v2/occupations` | 원본 occupation의 `{id, code, kind, name}` |
| `GET /api/v2/publications` | `{publication_id, source_state, capabilities}` |
| `GET /api/v2/publications/{publication_id}/alignments` | `{publication_id, source_enrichment_id, source_posting_id, source_current, accepted, competency:{id, code, kind, name}}` |

- 세 목록 라우트는 동일한 offset 기반 페이지 인수를 받습니다. `limit`은 선택 사항이며 기본값은
  `100`, 허용 범위는 `1`부터 `100`까지입니다. `offset`도 선택 사항이며 기본값은 `0`,
  `0` 이상의 정수만 허용합니다. 범위를 벗어난 요청은 원본 조회 전에 표준
  `422 VALIDATION_ERROR`로 거부됩니다.
- v2 응답은 `total`, 다음 페이지 URL, cursor를 제공하지 않습니다. 각 배열은 요청한 한
  페이지일 뿐이므로, 반환된 배열만으로 원본 전체 개수를 추정하지 마세요.
- `source_state`는 원본 상태를 그대로 전달합니다. 예를 들어 `READY`를 앱의
  `PUBLISHED` 편집 기준이나 분석 가능 상태로 해석하지 마세요.
- `accepted`는 원본 NCS 정렬 결정일 뿐, 사용자의 역량·직무 요구사항·충족률·로드맵
  결과가 아닙니다.
- `source_enrichment_id`, `source_posting_id`, `source_current`는 정렬 행의 확인된 원본
  enrichment 식별자·posting 식별자·현재 여부입니다. `source_current=false`를 삭제·철회·무효로
  해석하지 마세요. 대상 식별자는 중첩된 `competency.id`입니다.
- `capabilities.catalog`이 `AVAILABLE`이어도 `editorial_analysis`와 `route_planning`은
  별도로 `UNAVAILABLE`일 수 있습니다. 이 경우 v2 목록을 근거로 분석 요청이나
  로드맵 생성을 시도하지 마세요.
- Neo4j 원본 모드에서는 `POST /api/v1/analyses`가 재계산을 생성하기 전에 `503
  DATA_UNAVAILABLE`로 거부됩니다. 원본 카탈로그에 편집 검토 baseline·요구사항·활동
  template 계약이 없기 때문입니다. 이미 저장된 Neo4j 원본 작업은 `FAILED`와
  `NATIVE_SOURCE_UNSUPPORTED`로 종료되며, `READY` 분석이나 제안으로 바뀌지 않습니다.
- v2 occupation `id`를 v1의 `occupation_id`, `basis_version`, `release_id`로 변환하거나
  publication과 occupation의 소속 관계를 추정하지 마세요.
- 정렬 응답에 명시된 `source_enrichment_id`, `source_posting_id`, `source_current`,
  `competency.id` 이외의 원본 노드 ID, `postings`, 이름 출처 ID, raw payload JSON,
  reviewer/candidate/duty/evidence, 정렬 `decision_id`는 응답에 포함되지 않습니다. 클라이언트도
  이 데이터가 있다고 가정하지 마세요.

## 최신 원천 피드 (v2)

인증된 세션에서 `GET /api/v2/live/postings`, `/api/v2/live/postings/{posting_id}`,
`/api/v2/live/exam-sessions`를 사용할 수 있습니다. 별도 카탈로그 reader DB 연결이
설정되어야 하며, 없으면 `503 DATA_UNAVAILABLE`, 인증이 없으면 `401 UNAUTHENTICATED`입니다.
공고 목록에는 `q`, `ncs_category`, `region`, `open_on`(YYYY-MM-DD), 시험 일정에는
`qualification`, `from`, `to`(YYYY-MM-DD)를 전달합니다. 목록은 `limit=20`(1–100),
`offset=0`(0 이상)이며 `total`과 `items`, 적용된 `filters`, `sources`를 반환합니다.
알 수 없거나 중복된 쿼리 키는 422, 없는 공고는 404입니다. `sources`의 `run_id`와
`data_as_of`는 최신 READY 수집 실행의 출처를 표시합니다. 이 피드는 승인·봉인된
`/api/v2/catalog` 릴리스와 별개로 갱신되므로 페이지 사이 내용이 바뀔 수 있고,
원천 공고/시험 정보를 사용자 적격성·선호·선발 결과나 분석 근거로 해석하면 안 됩니다.
정확한 응답 필드는 [소스 카탈로그 API 계약](source-catalog-api.md)을 참고하세요.

`GET /api/v2/live/ncs-demand?ncs_prefix=200102`는 출처별 귀속 항목이 있는 가장 최근 READY 연결 발행물에서
공고와 연결된 NCS 능력단위의 근거 요약을 조회합니다. `ncs_prefix`는 2–8자리 숫자이며
생략할 수 있습니다. `limit=20`(1–100), `offset=0`(0 이상)을 지원합니다.
근거에는 공고 ID·제목·직무/업무 요약(최대 3건), 연결 건수, 자격 연결을 담지만
검토자 신원·사유·결정 ID는 노출하지 않습니다. 연결 근거를 사용자 적격성 판정이나
채용공고의 공식 요구조건으로 해석하지 마세요. 발행물의 `created_at`, 원천
`run_id`, `is_latest_publication`을 함께 확인하세요. 항목이 없는 최신 발행물 대신
과거 발행물의 연결이 제공될 수 있으며, **과거 근거는 오늘의 채용 수요가 아닙니다.**

## 현재 인증 상태 (fail-closed)

**Google 로그인은 현재 비활성화되어 있으며 `JOBTOLOGY_AUTH_ENABLED=false`로 유지합니다.**

- `GET /api/v1/occupations`는 공개 목록이며 인증이 필요하지 않습니다. 구성된 공개
  직무 스냅샷이 없으면 503을 반환합니다. 각 항목의 `name`은 표시 이름이며 매핑이
  없으면 `occupation_id`가 사용됩니다.
- 그 외 인증이 필요한 제품 및 v2 native catalog 라우트는 표준 `401 UNAUTHENTICATED`
  envelope으로 일관되게 거부됩니다.
- 신뢰되는 사용자 식별 헤더, 개발용 로그인 엔드포인트, 우회용 테스트 사용자는 없습니다.
  임의 헤더·cookie·fixture로 사용자를 가장할 수 없습니다.
- 현재 클라이언트가 사용할 로그인 시작 또는 세션 획득 흐름은 제공하지 않습니다. 인증이
  정식으로 재개되기 전에는 제품 쓰기/분석 흐름을 성공 경로로 구현하지 마세요.
- 화면 및 오류 UI 개발은 아래의 명시적 mock/preview fixture 모드를 사용하세요. fixture는
  제품 데이터를 만들거나 제품 라우트를 인증하지 않습니다.

## 핵심 사용 흐름

아래는 인증된 제품 배포에서 사용할 계약 흐름입니다. 현재 인증 비활성 상태에서는 제품
요청이 업무 처리 전에 `401`로 종료되므로, 개발 중에는 fixture 응답과 OpenAPI를 사용하세요.

### 1. 사용자 입력

프로필(`PUT /api/v1/me/profile`), 역량(`POST/PATCH /api/v1/me/capabilities`),
목표(`POST /api/v1/me/goals`)를 입력합니다. 경로 조건(`PUT /api/v1/me/route-preferences`)은 선택이며,
저장하지 않으면 주 10시간·일반 예산 기본값으로 분석합니다. 각 자원은 서버가 관리하는 `version`을
갖고, 이후 분석 요청 시 `expected_profile_version`으로 사용합니다.

#### 온보딩 "이미 해본 것" 체크리스트

직무를 고른 직후 보여줄 체크 항목은 `GET /api/v1/occupations/{occupation_id}/capability-checklist`
로 조회합니다(세션 불필요). 응답은 `{occupation_id, checklist_version, groups: [{label, items:
[{item_id, label}]}]}`이며 NCS 단위 ID는 노출하지 않습니다. 현재 승인된 직무 요구에 연결이 없는
항목은 숨겨지고, 체크리스트가 없는 직무는 `404`입니다. 이때 단계를 건너뛰세요.

체크 결과는 한 번에 저장합니다.

```text
PUT /api/v1/me/capabilities/onboarding
{ "expected_profile_version": 3, "occupation_id": "BACKEND_DEVELOPER", "item_ids": ["B1", "B6"] }
→ 200 { "profile_version": 4, "saved_count": 3 }
```

- 한 트랜잭션에서 저장하고 프로필 버전은 **한 번만** 올라갑니다. 다음 요청에는 응답의
  `profile_version`을 쓰세요.
- 이전 온보딩 응답을 **교체**합니다. 빈 `item_ids`는 이전 응답을 모두 지웁니다. "내 정보"에서
  직접 입력한 역량은 바뀌지 않습니다.
- 저장된 역량은 `category: "onboarding"`, `verification: "SELF_REPORTED"`입니다.
  `saved_count`는 저장된 역량 수로, 항목 하나가 여러 역량에 연결되면 항목 수보다 큽니다.
- 알 수 없는 `item_id`는 `422`, 버전 불일치는 `409`입니다. `Idempotency-Key`를 지원합니다.

### 2. 분석 요청 (202 비동기)

```text
POST /api/v1/analyses
Content-Type: application/json
Idempotency-Key: <클라이언트 생성 UUID>

{ "goal_id": "...", "expected_profile_version": 3, "basis_type": "EDITORIAL" }
```

- 같은 목표·같은 프로필 버전으로 진행 중이거나 완료된 계산이 있으면 새로 계산하지 않고 그 요청을
  그대로 돌려줍니다. 화면 진입 시에는 먼저 `GET /api/v1/dashboard?goal_id=...`의
  `state`/`analysis_id`로 저장된 결과를 조회하고, 결과가 없을 때만 분석을 요청하세요.
  프로필·역량·조건이 바뀌면 서버가 자동으로 재계산합니다.
- 성공 시 `202 Accepted`와 함께 `{recompute_request_id, state, status_url}`을
  반환합니다. `status_url`이 폴링 주소입니다.
- 재계산은 비동기입니다. 응답이 왔다고 분석이 끝난 것이 아닙니다.

### 3. 재계산 폴링

`GET /api/v1/recomputations/{recompute_request_id}` 를 약 2초 간격으로 폴링합니다.

내부 저장 상태는 `PENDING → RUNNING → READY 또는 FAILED`이며, 폴링 응답은
저장된 `READY`를 프런트엔드용 `COMPLETED`로 변환합니다.

- `COMPLETED`: 성공 종료 상태입니다. `analysis_id`와 `resulting_analysis_id`
  (같은 값), `proposal_id`가
  채워져 있습니다. **폴링을 중단하세요.**
- `FAILED`: 실패 종료 상태입니다. `error_code`로 실패 원인을 안내합니다. 특히
  `NATIVE_SOURCE_UNSUPPORTED`는 Neo4j 원본에 검증된 편집 baseline·활동 template이 없어
  작업이 실패한 경우입니다. 폴링을 중단하고 사용자에게 안내하세요.
- 그 외(`PENDING`, `RUNNING`): 계속 폴링합니다.

종료 상태(`COMPLETED`/`FAILED`)에 도달하면 반드시 폴링을 멈춰야 합니다.

분석 결과 상세는 `GET /api/v1/analyses/{analysis_id}` 로 조회합니다. 기존 원본
`results`에 더해 `result`에는 필수/우대 충족률(`required_pct`, `preferred_pct`),
아직 충족하지 않은 역량 `skills`가 필수 우선·이름순으로 포함됩니다. 각 역량의
`demand_pct`·NCS 수준·학습 시간은 승인된 직무 메타데이터가 있을 때만 표시되며,
20시간 등의 추정치는 `achievement`에 `(추정)`으로 명시됩니다. 저장 결과가
없으면 `result`는 null입니다.

`demand_pct`(공고 요구 정도)는 해당 직무 역량에 NCS 링크가 연결된 공고(job_alio·
nara_job, 중복 제거) 중 이 역량을 요구한 공고의 비율(0–100 정수)입니다. 근거로
`demand_postings`(이 역량 연결 공고 수)와 `demand_base`(직무 연결 공고 수)를 함께
내려줍니다. `demand_base`가 5건 미만이면 표본 부족으로 `demand_pct`는 null이지만
두 건수는 그대로 제공되므로 "N건 중 M건"으로 표시할 수 있습니다. 근거 데이터 자체가
없으면 세 값 모두 null이며, null은 0%와 다르게(미표시) 처리해야 합니다. 링크 근거는
검토된 과거 발행분일 수 있으므로 "오늘의 수요"로 표현하지 마세요.

### 3-1. AI 챗봇과 역량 후보

`GET /api/v1/chat/status` → `{available}`. `false`이면 AI 대화가 설정되지 않은 것이니 기존 화면을
유지하세요. `POST /api/v1/chat/messages`에 최근 대화(최대 20개, 마지막은 사용자)를 보내면
`{reply, occupation_id, candidates: [{entity_id, label, evidence_quote}]}`를 돌려줍니다.

- 서버는 대화를 저장하지 않습니다(무상태). 클라이언트가 최근 대화를 다시 보냅니다.
- 요청에 `"mode": "onboarding"`을 넣으면 상담 대신 아직 이야기하지 않은 역량 영역을 하나씩 묻는 온보딩
  질문으로 답합니다(기본 `"counsel"`).
- 후보는 활성 목표 직무의 승인된 요구 역량 중 아직 보유하지 않은 것만, 사용자 발화를 글자 그대로
  인용한 근거와 함께 나옵니다. **자동 저장하지 않습니다.**
- 사용자가 확인하면 `POST /api/v1/me/capabilities`로 `category: "chat"`, `raw_text: label`,
  `details: {"source": "chat", "evidence_quote": ...}`를 저장합니다. 원문 대신 근거 문장만 남깁니다.
- LLM 미설정·실패 시 `503`, 사용자당 분당 20회를 넘으면 `429`입니다.

### 4. 로드맵 제안 확인

`GET /api/v1/route-proposals/{proposal_id}` 로 제안된 로드맵(단계, 예상 시간,
선행 관계)을 확인합니다. `feasibility`는 `FEASIBLE`, `RISKY`, `PARTIAL`, `INFEASIBLE` 중 하나입니다.
`PARTIAL`은 목표 기간 안에 필수 역량을 모두 배울 수 없어 **필수 역량을 최대한 많이 다루는 일부
경로**만 만든 경우입니다. 단계는 정상적으로 저장·활성화할 수 있으므로, 화면에 "기간 안에 가능한
범위의 일부 경로"임을 함께 안내하세요. 다루지 못한 필수 요구는 trace `outputs.unmet_required_requirement_keys`에
있습니다. 사용자가 이 제안을 검토·수용하는 UI를 이 단계에
배치하세요. 경로 계산 근거는 `GET /api/v1/traces/{trace_id}` 로 추적할 수
있습니다.

### 5. 로드맵 저장 후 명시적 활성화

제안을 저장하면 항상 `DRAFT` 상태로 시작합니다:

```text
POST /api/v1/roadmaps   → 201, state: "DRAFT"
```

사용자가 명시적으로 확인하면 활성화합니다:

```text
PATCH /api/v1/roadmaps/{roadmap_id}
```

```json
{
  "operation": "ACTIVATE",
  "expected_roadmap_version": 1,
  "expected_profile_version": 3
}
```

`operation`은 `ACTIVATE`, `ARCHIVE`, `RENAME`을 지원합니다. 실수로 즉시
활성화되는 일이 없도록, 저장과 활성화는 반드시 별도 사용자 동작으로 구성하세요.
`GET /api/v1/roadmaps`와 `GET /api/v1/roadmaps/{roadmap_id}`의 각 로드맵에는
기존 `state`/`roadmap_version`과 동일한 `status`/`version` 별칭이 포함됩니다.
단계의 `title`은 연결된 제안의 같은 `step_key`에서 읽고, `description`은 첫
완료 기준 문자열입니다(없으면 null). 제안 제목을 임의 생성하지 않습니다.

### 6. 단계 진행 표시

로드맵 단계 상태를 갱신합니다:

```text
PATCH /api/v1/roadmaps/{roadmap_id}/steps/{step_id}
```

```json
{
  "state": "COMPLETED",
  "expected_roadmap_version": 2,
  "expected_profile_version": 3
}
```

`state`는 `TODO`, `IN_PROGRESS`, `COMPLETED`이며, 허용되는 전이는 다음뿐입니다.

| 현재 상태 | 다음 상태 |
|---|---|
| `TODO` | `IN_PROGRESS`, `COMPLETED` |
| `IN_PROGRESS` | `TODO`, `COMPLETED` |
| `COMPLETED` | `IN_PROGRESS`, `TODO` |

- `TODO`에서 곧바로 `COMPLETED`로 완료할 수 있습니다(“완료했어요” 토글).
- 완료를 되돌리는 전이(`COMPLETED → IN_PROGRESS` 또는 `COMPLETED → TODO`)는
  완료로 얻은 역량을 회수(`REVOKED`)하고 재분석을 예약합니다.
- 단계 상태 변경은 로드맵이 `ACTIVE` 상태일 때만 가능하고, 성공 시 로드맵
  버전과 프로필 버전이 모두 증가합니다. 위 예시의 버전 값(`2`, `3`)은 설명용
  값이므로 하드코딩하지 말고, 변경할 때마다 자원을 다시 조회해 얻은 현재
  버전으로 요청하세요.

### 7. 대시보드

`GET /api/v1/dashboard` 로 진행 중인 로드맵, 다음 할 일, 최신 분석 상태를
조회합니다. 폴링 주기는 이 엔드포인트 기준으로 구성하세요.

## 공통 에러 규약

API가 처리하는 오류는 동일한 envelope을 반환합니다:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed",
    "details": [{ "location": ["body", "field"], "code": "missing" }],
    "request_id": "00000000-0000-0000-0000-000000000001"
  }
}
```

다만 이 envelope은 애플리케이션이 처리하는 오류에만 적용됩니다. 네트워크
장애, 프록시 게이트웨이 오류, 처리되지 않은 서버 오류는 JSON이 아닌 응답
(빈 본문, 일반 텍스트 `500` 등)으로 올 수 있고 이때는 `request_id`도 없을 수
있습니다. 프론트엔드 오류 처리 권장 순서:

1. 응답의 `Content-Type`을 먼저 확인하고 JSON이면 파싱을 시도하세요.
2. JSON이 아니거나 파싱에 실패하면 HTTP 상태 코드와 상태 문구
   (`status`/`statusText`)를 기준으로 안내 문구를 구성하세요.
3. 응답 본문은 있는 그대로의 보조 텍스트로만 다루고, 검증 없이 화면에
   그대로 출력하지 마세요.
4. `X-Request-ID` 헤더는 envelope과 별도로 보존해 오류 보고에 사용하세요.
   헤더가 없는 응답도 가정해야 합니다.

- `code`: 아래 표의 기계 판독용 코드.
- `message`: 사람이 읽는 요약(표시용 문구는 프론트엔드가 `code` 기준으로
  작성하는 것을 권장).
- `details`: 검증 오류의 위치와 원인(주로 `422`에서 사용).
- `request_id`: 이 요청의 추적 ID. 응답 헤더 `X-Request-ID`에도 같은 값이
  있습니다. 오류 보고 시 이 값을 첨부하세요.

주요 코드:

| HTTP | code | 프론트엔드 처리 |
|---|---|---|
| 401 | `UNAUTHENTICATED` | 로그인 필요 안내 (현재는 인증 비활성화로 인한 거부) |
| 403 | `FORBIDDEN` | 다른 사용자 자원 접근 등, 요청 중단 |
| 404 | `NOT_FOUND` | 없는 자원, 뒤로 가기 등 복구 UI |
| 409 | `VERSION_CONFLICT` | 아래 낙관적 동시성 참고 |
| 409 | `IDEMPOTENCY_CONFLICT` | 같은 키에 다른 payload 재사용, 키 재발급 필요 |
| 409 | `IDEMPOTENCY_IN_PROGRESS` | 동일 키 처리 진행 중, 잠시 후 재시도 |
| 422 | `VALIDATION_ERROR` | `details` 기준으로 입력 폼 교정 |
| 500 | `INTERNAL_ERROR` | 폴백 처리: JSON envelope이 아닐 수 있음(위 참고) |
| 503 | `DATA_UNAVAILABLE` | 분석 입력 미구성 등 서버 측 준비 부족 |

### 낙관적 동시성(버전 충돌) 처리

변경 요청의 `expected_*_version`이 서버 현재 값과 다르면 `409 VERSION_CONFLICT`를
반환합니다. 처리 순서:

1. `409`를 받으면 사용자 입력을 폐기하지 말고,
2. 최신 자원을 다시 조회해(`GET`) 새 버전과 서버 상태를 확인한 뒤,
3. 필요하면 병합 UI를 보여주고 새 버전으로 재요청하세요.

### 멱등성 (Idempotency-Key)

`POST` 엔드포인트에는 클라이언트 생성 UUID를 `Idempotency-Key` 헤더로 보내는
것을 권장합니다. 같은 키와 같은 payload에 대해 서버는 최초 응답을 24시간
동안 재생합니다. 네트워크 재시도, 이중 제출 방지에 사용하세요.

## 개발용 fixtures와 mock 샘플

자격 증명 없이 프론트엔드를 개발할 수 있는 읽기 전용 모드 두 가지가 있고,
각각 별도의 환경 변수로 독립적으로 활성화됩니다. mock 샘플을 켜도 레거시
프리뷰가 함께 노출되지 않으며, 그 반대도 마찬가지입니다.

### FE mock 샘플 (권장)

```sh
JOBTOLOGY_ENABLE_FE_MOCK_SAMPLES=true uv run uvicorn jobtology_be.main:app --reload
```

- `GET /api/v1/dev/mock/samples`: 실제 DTO 형상과 일치하는 mock 전용 예시
  번들입니다. `202` 승인/멱등성 재생 응답 형상과 `401`, `409`(`VERSION_CONFLICT`)
  오류 envelope 예시를 함께 담고 있어 오류 처리 UI를 구현할 때 기준으로
  사용하세요.
- 응답은 `read_only`와 `contract_scope: MOCK_ONLY_NOT_PRODUCT_DATA`로 명시적으로
  표시됩니다. 계산·저장되지 않은 예시이며 제품 데이터가 아닙니다.

### 레거시 프리뷰

```sh
JOBTOLOGY_ENABLE_FIXTURES=true uv run uvicorn jobtology_be.main:app --reload
```

- `GET /api/v1/dev/analysis`: 예시 역량 분석 프리뷰
- `GET /api/v1/dev/route-proposal`: 예시 로드맵 제안 프리뷰

mock/preview 응답은 화면·상태 처리 테스트용 정적 예시일 뿐이며, 인증된 제품
API 또는 저장된 사용자 데이터를 대체하지 않습니다. 두 모드 어느 쪽을 켜도
제품 트래픽이 인증되지는 않습니다. `/api/v1/me/*`를 포함한 제품 라우트는
표준 `401 UNAUTHENTICATED` envelope으로 그대로 거부되며, 개발용 로그인이나
신뢰된 사용자 식별 헤더도 존재하지 않습니다.

프로덕션 설정에서는 두 플래그 모두 거부됩니다.

## 로컬 실행 환경 변수 요약

| 변수 | 용도 |
|---|---|
| `JOBTOLOGY_DATABASE_URL` | 애플리케이션 상태 저장용 PostgreSQL 연결 |
| `JOBTOLOGY_CORPUS_SNAPSHOT_PATH` | 분석 근거 코퍼스 로컬 스냅샷 파일 경로 |
| `JOBTOLOGY_CORPUS_SOURCE` | `local_json`(기본값) 또는 `neo4j_query_api` 원본 선택. Neo4j는 v2 읽기 전용 카탈로그만 제공 |
| `JOBTOLOGY_DB_LINK` | Neo4j 원본 연결 주소. `host:port`만 지정하면 아래 protocol도 필요 |
| `JOBTOLOGY_DB_PROTOCOL` | bare `DB_LINK`에 붙일 Neo4j/Bolt protocol (`bolt+s://` 등) |
| `JOBTOLOGY_DB_PASSWORD` | Neo4j Query API 자격 증명. 서버 환경에만 보관하고 클라이언트에 노출하지 않음 |
| `JOBTOLOGY_ENABLE_FIXTURES` | `true`이면 `/api/v1/dev` 레거시 프리뷰 라우트 활성화 |
| `JOBTOLOGY_ENABLE_FE_MOCK_SAMPLES` | `true`이면 `/api/v1/dev/mock/samples` mock 샘플 라우트 활성화 |
| `JOBTOLOGY_CORS_ORIGINS` | 허용 프론트엔드 origin 목록(JSON 배열 문자열) |
| `JOBTOLOGY_AUTH_ENABLED` | Google 로그인 활성화 여부. 현재는 `false`로 유지하며, 비활성 상태의 제품 라우트는 fail-closed `401`을 반환 |
| `JOBTOLOGY_ENVIRONMENT` | `development` / `test` / `production` |

환경 변수 전체 목록과 최신 기본값은 `.env.example` 과
[settings.py](../src/jobtology_be/settings.py) 를 참고하세요.

## 문서 링크 모음

- 대화형 API 문서(Swagger UI): [\/api\/docs](/api/docs)
- 열람용 API 문서(ReDoc): [\/api\/redoc](/api/redoc)
- OpenAPI JSON: [\/api\/openapi.json](/api/openapi.json)
- 이 문서의 원본 markdown: 저장소 `docs/fe-integration.md`
