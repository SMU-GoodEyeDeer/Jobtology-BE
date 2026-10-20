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

제품 인증은 Google OAuth로 제공하며 게스트 계정은 만들지 않습니다. 승인된 직무별
스냅샷을 입력으로 분석 및 CP-SAT 경로 계획을 실행합니다. Neo4j 원본 카탈로그 읽기는
별도 계약입니다. 필드·상태·응답 형식의 최종 기준은 배포 서버의 `/api/openapi.json`입니다.

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

## Google 인증과 계정 관리

로그인은 `GET /api/v1/auth/google/login`에서 시작합니다. `openid profile` 범위를 요청하고,
검증한 Google ID 토큰의 `sub`로 계정을 식별합니다. 이름은 표시용이며 계정 연결에 쓰지 않습니다.

- `GET /api/v1/auth/session`: `user_id`, `csrf_token`, 선택적인 `display_name`을 반환합니다.
  이름이 없으면 일반적인 호칭을 표시하세요. UUID나 Google subject를 이름으로 표시하지 마세요.
  이전 계정은 다시 로그인한 뒤 이름이 채워집니다.
- `POST /api/v1/auth/logout`: 현재 세션을 폐기합니다.
- `DELETE /api/v1/me/account`: 본인의 Jobtology 계정과 프로필·입력·분석·로드맵·작업·모든 세션을
  한 트랜잭션으로 영구 삭제하고 쿠키를 지웁니다. Google 계정 자체는 삭제하지 않습니다.
  UI에서 영구 삭제 확인을 받은 뒤 호출하세요. 성공은 204이며 로컬 세션을 즉시 비웁니다.
  실패하면 세션을 유지하고 재시도할 수 있게 하세요.

변경 요청은 세션 쿠키와 `X-CSRF-Token`이 필요합니다. 같은 origin의 `/api` 상대 경로를 사용하세요.
Google 이름을 받기 위해 기존 Google Cloud 클라이언트를 교체할 필요는 없습니다.

## 주차 플랜

저장된 솔버 일정과 선행 단계가 추천 플랜의 기준입니다. 브라우저에서 별도 일정을 재계산하지 마세요.
완료 상태는 일정 수정·초기화와 별개로 보존됩니다.

| 엔드포인트 | 동작 |
|---|---|
| `GET /api/v1/roadmaps/{roadmap_id}/plan` | 저장된 설정 또는 원본 솔버 일정을 반환합니다. |
| `POST /api/v1/roadmaps/{roadmap_id}/plan/preview` | 설정을 저장하지 않고 미리 계산합니다. `null`은 원본 솔버 일정을 반환합니다. |
| `PUT /api/v1/roadmaps/{roadmap_id}/plan` | 사용자 설정과 경고를 저장 후 반환합니다. |
| `DELETE /api/v1/roadmaps/{roadmap_id}/plan` | 설정을 삭제하고 원본 솔버 일정을 반환합니다. |

모두 인증이 필요하며 POST/PUT/DELETE에는 `X-CSRF-Token`을 전송하세요.
타인 소유·없는 로드맵은 `404 NOT_FOUND`입니다. 설정 본문은 아래 다섯 필드가 필수입니다.

```json
{
  "expected_version": 0,
  "weekly_hours": 12,
  "rest_weeks": [10, 11],
  "order": ["로드맵의 모든 step_id를 원하는 순서로 나열"],
  "hours": {"시간을 바꿀 step_id": 24}
}
```

- 모든 응답의 `version`은 설정 전용 버전입니다(처음 0). PUT/미리보기는 마지막으로 읽은 값을
  `expected_version`에, DELETE는 `?expected_version=...`에 보냅니다. 저장/초기화는 버전을 올리며,
  오래된 저장/초기화는 `409`이므로 최신 플랜을 읽은 후 사용자가 변경을 다시 적용해야 합니다.
  미리보기는 버전을 올리지 않습니다. 초기화 후에도 버전은 0으로 돌아가지 않습니다.
- 완료 단계의 시간 변경은 `422`입니다. 저장/초기화는 완료 단계의 기존 날짜·시간 배분을 유지하고,
  새 사용자 일정의 미완료 활동만 현재 시점 이후로 다시 배치합니다.
- `weekly_hours`: 1~60 정수. `rest_weeks`: 중복 없는 1~104주 정수 배열.
- `order`: 모든 단계 ID의 정확한 순열. `hours`: 단계 ID 일부와 1~400 정수 시간.
  추천 시간과 같은 값은 별도 수정으로 보지 않습니다.
  순서는 실행 가능한 활동 간의 우선순위이며 새 선행 관계를 만들지 않습니다. 실제 선행 단계가
  완료되어야 후속 단계를 시작합니다. 사용자 일정과 원본 날짜가 없는 추정 일정에서는 최대
  **2개 활동**만 진행 중인 작은 학습 묶음으로 배치하고, 활동 하나가 끝나면 다음 활동을 시작합니다.
  진행 중인 독립 활동은 주간 시간 예산을 나눠 쓰며 전체 활동을 동시에 시작하지 않습니다.
  순서를 바꾸지 않았다면 원본 시작 간격을 학습 시간 축의 부드러운 선호로 유지하지만,
  진행 중인 활동이 없어도 원본 간격만 맞추려고 시간을 비워 두지는 않습니다.
  순서를 직접 바꾸면 사용자가 정한 우선순위를 따릅니다. 주간 총 시간과 쉬는 주 제약은 그대로
  적용하고, 저장된 솔버 원본·추천 초기화·변경 없는 저장은 원본 날짜를 유지합니다.
- `schedule_source`: `SOLVER`는 저장된 원본 날짜, `CUSTOM`은 사용자 설정으로 계산한 주간 일정,
  `FALLBACK`은 원본 날짜가 없는 단계의 추정 일정입니다.
- `steps[].start_date/end_date`: 솔버 모드에서는 저장된 정확한 날짜입니다. 주차 표시에 맞춰
  월요일·일요일로 덮어쓰지 마세요. 시간대는 목표의 IANA 시간대입니다. `planned_start_at`과
  `planned_end_at`은 정확한 `[시작, 종료)` 구간입니다. 날짜/주차의 종료는 마지막 점유일이므로
  자정 종료를 다음 날의 학습으로 표시하지 않습니다.
- `steps[].week_allocations`: 저장된 솔버 시간 슬롯 또는 사용자 설정/추정 일정의 정확한 주차별
  `{week, hours}` 배분입니다.
  브라우저에서 전체 시간을 다시 직렬 배분하지 마세요. 원본 시간 슬롯이 저장되지 않은 솔버
  일정에서는 빈 배열일 수 있으며, 원본 날짜 범위를 기준으로 표시합니다.
- `steps[].title`과 `completion_criteria`는 저장된 활동과 완료 기준입니다. 포트폴리오 활동의
  프로젝트 결과물·README 기준을 상세 화면에 표시하세요. `name`은 관련 역량 이름입니다.
- `steps[].prerequisite_step_ids`: 저장된 실제 선행 단계입니다. NCS 수준으로 선행 관계를 추측하지 않습니다.
  `unmet_prerequisite_step_ids`와 `has_dependency_violations`는 순서·원본 시간의 위반 여부입니다.
- `warnings`: `TARGET_DATE_EXCEEDED`, `PREREQUISITE_VIOLATION`, `MISSING_SOLVER_SCHEDULE`과
  표시용 메시지를 반환합니다. `deadline_exceeded`, `days_past_target`도 제공됩니다.
  목표를 넘는 사용자 일정은 경고와 함께 저장할 수 있습니다.
- `is_customized`, `saved_at`: 수정 여부와 저장 시각입니다. 저장 전 미리보기의 `saved_at`은 null입니다.
- `recommended_completion_date`는 원본 일정의 완료일입니다. 단계 시간은 제안에 고정한 추정치를 우선하며,
  현재 카탈로그의 변경으로 원본 일정이 달라지지 않습니다.

## 목표와 계획 선호

목표 저장 시 유효한 IANA 시간대, 시간대가 있는 미래 기한, 366일 이내 기한 및 지원하는 직무를
검증합니다. 잘못된 값은 저장 전에 필드별 `422 VALIDATION_ERROR`를 반환합니다.

목표가 없어도 내 정보의 목표 관리에서 추가할 수 있습니다. 활성 목표가 있으면 추가 목표는
임시저장으로 만들고, 기존 목표를 보관한 뒤 원하는 목표를 활성화합니다. 목표 직무 또는 모드를
바꾸거나 활성 목표를 보관하면 기존 활성 로드맵은 보관됩니다. 단계 진행과 사용자 설정은 유지됩니다.
마감일만 바꾸면 기존 일정과 진행을 유지하며 주차 플랜 경고는 현재 목표일을 기준으로 계산합니다.
활성 목표를 새로 만들거나 수정한 뒤에는 `/api/v1/analyses`로 현재 프로필 버전의 분석을 요청하세요.
목표 저장 성공 뒤 분석 요청이 실패한 경우 목표를 다시 생성하지 말고 분석만 재시도하세요.
분석은 직무가 정해진 `ACTIVE` + `TARGETED` 목표에만 요청합니다. 탐색 목표나 보관·임시저장
목표를 제출하면 `409 ACTIVE_TARGETED_GOAL_REQUIRED`를 반환합니다. 이때 일반 오류 대신
목표 설정 안내를 표시하세요. 없는 목표와 다른 계정의 목표는 동일하게 `404 NOT_FOUND`입니다.

목표 삭제는 `DELETE /api/v1/me/goals/{goal_id}`에 JSON 본문
`{"expected_profile_version": 현재버전}`을 보내며, `goal_id`와 새 `profile_version`을 반환합니다.
확인 후 목표와 관련 분석·로드맵·계획·진행 기록, 대기/실행 중인 분석 작업을 영구 삭제합니다.
계정, 프로필, 등록 역량과 다른 목표는 유지하며 다른 목표를 자동 활성화하지 않습니다.
삭제 뒤 목표 목록과 선택 항목을 갱신하고, 목표가 없으면 설정 안내를 표시하세요.

`needs_portfolio`는 “포트폴리오 프로젝트 선호”, `career_switch`는 “기초 학습 우선”입니다.
기존 API 키는 호환성을 위해 유지하며 사용자의 현재 상태를 추측하지 않습니다.
제품 경로의 가격 모델은 현재 0원 가정입니다. 이 가정에서 예산 제한만으로 활동을 제외하지 않으며,
실제 유료 강좌의 가격을 검증했다는 의미는 아닙니다. 시간·기한·역량·선행 제약은 계속 적용됩니다.

배포 전 마이그레이션 관리자 자격으로 **`uv run alembic upgrade head`** 명령을 실행하세요.
제한된 애플리케이션 DB 계정에는 스키마 변경 권한을 주지 않습니다.

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

`GET /api/v2/live/ncs-units/{base_code}`는 NCS 능력단위 상세를 조회합니다.
`base_code`는 ASCII 숫자 10자리(예: `2001020205`)여야 하며 쿼리 파라미터는
허용하지 않습니다(위반 시 `422 VALIDATION_ERROR`). 응답은
`{contract_version: "hop-live-source-v1", sources, item}`이며 `item`은
`{base_code, code, name, level, definition}`입니다. `code`는 버전을 포함하고,
`level`·`definition`은 원천에 값이 없으면 null입니다. DB의 `item: null`은
성공 응답이 아니라 `404 NOT_FOUND`로 변환됩니다. 인증은 필수이며, DB 함수
`catalog.live_ncs_unit_v1(text)` 또는 reader 권한이 없거나 원천 조회가 실패하면
`503 DATA_UNAVAILABLE`입니다. 최신 READY 원천의 설명이므로 분석 당시 승인된
버전과 다를 수 있습니다. 분석의 `ncs_base_code`가 null이면 조회를 생략하세요.

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

#### 온보딩·내 정보의 구조화된 내 스펙

온보딩과 이후 수정은 같은 편집기를 사용합니다. 기존 체크리스트 조회·일괄 교체 경로는
종료했습니다. 이번 교체는 소유자가 기존 스펙 초기화를 요청한 변경이며 이전 입력 형식을
변환하는 호환 계층이나 스키마 마이그레이션은 없습니다. BE와 FE를 함께 배포하세요.

- `GET /api/v1/skills?q=Python&occupation_id=BACKEND_DEVELOPER&limit=20`: 인증된 검색입니다.
  `occupation_id`는 선택이며 없으면 전역 검색입니다. 승인된 NCS 단위명·기술 별칭과 O*NET
  35개 핵심 역량의 원래 영어 이름·Jobtology 한국어 번역을 검색합니다. 원래 식별자와 출처를
  보존하며 번역만으로 O*NET을 NCS 요구와 동등하다고 처리하지 않습니다.
- `POST /api/v1/me/capabilities`, `PATCH /api/v1/me/capabilities/{id}`:
  `expected_profile_version`, `category` (`skill`, `certification`, `test`, `project`, `experience`),
  `raw_text` (제목), `proficiency` (`FAMILIAR`, `GUIDED`, `INDEPENDENT`, `PRODUCTION`),
  `details`를 제출합니다. PATCH도 편집 내용 전체를 제출합니다. 응답은 ID와 새 프로필 버전이며
  성공 후 상세 GET으로 전체 레코드·연결 결과를 조회하세요. POST의 멱등성 키는 응답 유실 시
  같은 원래 버전과 본문으로 재사용하고, 저장 후 조회 실패는 GET만 재시도합니다.
- `details`: 선택한 `competency_ids` (최대 12개), `evidence` (최대 2000자),
  `source` (`MANUAL` / `LLM_ASSISTED`), `issuer`, `credential_code`, `issued_on`, `expires_on`,
  `score`, `score_max`, `score_unit`, `organization`, `role`, `started_on`, `ended_on`,
  `ongoing`, HTTP(S) `url`을 구조화합니다. 자격증은 발급기관, 시험 점수는 수치가 필요합니다.
  종료일·만료일·점수 범위는 저장 시 검증합니다. 스펙 제목·점수·자격증명만으로 역량이
  자동 충족되거나 실제 자격이 검증되는 것은 아닙니다.
- 목록·상세 GET의 `matching`은 **현재 ACTIVE 목표** 기준의 연결 가능성을 보여줍니다.
  `MATCHED`, `NEEDS_CLARIFICATION`, `UNUSED`, `EXPIRED`와 연결 역량·출처·계획 반영치를
  함께 표시하세요. 관련 역량이 없는 기록은 보관하되 현재 분석에 반영하지 않습니다.
- 역량 선택과 20자 이상 수행 근거가 있을 때만 계획 반영치 25/50/75/100%를 수행 수준에
  따라 적용합니다. 동일 역량은 조건을 충족하는 가장 강한 기록 하나를 쓰며 합산하지 않습니다.
  만료되었거나 아직 발급되지 않은 자격·시험 기록은 새 분석에서 0입니다. 날짜 판정은 분석 기준일과
  활성 목표 시간대를 따릅니다. 내부 로드맵 완료 결과는 별도 완료 이벤트 출처를 유지합니다.
- `POST /api/v1/chat/messages`의 `mode: "spec"`는 사용자가 명시적으로 경험 설명을 요청했을
  때만 호출합니다. 허용된 역량 ID, 사용자 발화의 인용과 기록 종류 제안을 후보로 반환하며 저장하지 않습니다.
  사용자가 후보·근거·수행 수준을 확인한 후 동일한 스펙 저장 API를 사용합니다. AI가
  비활성화되거나 실패해도 수동 검색과 저장은 계속 사용할 수 있습니다.

부분 인정은 `MODELED_COMPETENCY_CREDIT_V1`이라는 **제품 계획 가정**이며 검증된 숙련도·
취업 확률이 아닙니다. 기존 완전 충족률(`required_pct` / `preferred_pct`)과 가중 계획 지표
(`weighted_required_pct` / `weighted_preferred_pct`)를 구별하세요. `PARTIAL` 요구는 계속
부족 역량에 보이며 남은 학습시간을 `ceil(원본 시간 × (1 − 인정치))`로 추정합니다(최소 1시간).
여러 요구를 다루는 활동은 가장 낮은 인정치를 사용합니다. 실제 솔버·제안·주차 플랜은 같은
남은 시간을 사용하고, 줄인 시간은 공식 최소 훈련시간으로 표시하지 않습니다. 승인된 원본
카탈로그와 서명은 변경하지 않습니다. 프로필 변경 후 새 분석이 필요하며 기존 활성 로드맵과
진행 상태는 자동으로 덮어쓰지 않습니다.

O*NET® 31.0 데이터: USDOL/ETA, [CC BY 4.0](https://www.onetcenter.org/license_db.html).
한국어 표시는 Jobtology가 추가한 번역으로 공식·승인된 번역이 아닙니다.

### 2. 분석 요청 (202 비동기)

```text
POST /api/v1/analyses
Content-Type: application/json
Idempotency-Key: <클라이언트 생성 UUID>

{ "goal_id": "...", "expected_profile_version": 3, "basis_type": "EDITORIAL" }
```

- 동일한 목표·프로필·코퍼스 release·입력 인정치·조건·기준일·주간 캘린더 용량이면 진행 중인
  요청을 공유합니다. 완료 결과는 같은 유효 컨텍스트일 때만 재사용합니다. 기준일이나 release,
  유효기간에 따른 인정치가 바뀌면 새 요청을 생성합니다. 완료 후 수동 재분석에는 `refresh: true`를
  추가하세요. 이 경우에도 같은 입력으로 진행 중인 작업은 공유하며, 같은 `Idempotency-Key`는
  첫 응답을 재생합니다. 새 재분석에는 새 키를 사용하세요. 화면 진입 시에는 먼저 `GET /api/v1/dashboard?goal_id=...`의
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

역량 상세 패널을 위해 `result.skills[]`에 다음 필드가 추가됩니다. 기존 필드와
필수 우선·이름순 정렬은 유지하며 `SATISFIED` 항목은 계속 제외합니다.

| 필드 | 의미 / 값이 없을 때 |
|---|---|
| `requirement_key` | 저장된 요구사항 키(예: `BACKEND_DEVELOPER:2001020205`), 항상 제공 |
| `ncs_base_code` | 승인된 요구사항의 10자리 NCS 기본 코드, 메타데이터가 없으면 null |
| `status` | 저장된 `UNMET` 또는 `NEEDS_INPUT`, 항상 제공 |
| `ncs_level` | NCS 수준 정수, 알 수 없으면 null |
| `estimated_hours` | 학습 시간 정수, 알 수 없으면 null |
| `hours_basis` | `OFFICIAL`(공식 시간) / `ESTIMATED`(추정 시간), 알 수 없으면 null |
| `qualifications` | 승인된 입력의 **선택된 NCS 버전**에 연결된 Q-Net 자격 `{code, name, display_name, kind}` 배열. `name`은 원본(null 가능), `display_name`은 화면용 이름(항상 제공), `kind`는 `NATIONAL_TECHNICAL`(국가기술자격) / `WORK_LEARNING`(일학습병행자격) / null. 일학습병행은 `SW개발_L5_25V2` → `SW개발 5수준`처럼 풀고, 같은 이름·수준은 최신 기준 하나만 남깁니다. 국가기술자격이 먼저 정렬됩니다 |

자격 연결이 없거나 메타데이터를 사용할 수 없으면 `qualifications`는 `[]`입니다.
신규 분석의 수요·NCS 수준·자격·학습 시간은 결과에 고정됩니다. 과거 결과는 release ID가 정확히
일치하는 메타데이터만 보충하며, 다른 release의 현재 메타데이터를 섞지 않습니다.
메타데이터가 없을 때는 `ncs_base_code`, `ncs_level`, `estimated_hours`, `hours_basis`가
모두 null이지만 `requirement_key`와 `status`는 저장값을 유지합니다. null을 0으로
치환하거나 자격 연결을 취득 자격·응시 적격성으로 해석하지 마세요.

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
`{reply, occupation_id, candidates: [{entity_id, label, evidence_quote}], suggestions: [string]}`를 돌려줍니다.
`suggestions`는 이 대화에 이어 사용자가 물어볼 만한 짧은 질문 최대 4개입니다(추천 질문 칩으로 사용).

- 서버는 대화를 저장하지 않습니다(무상태). 클라이언트가 최근 대화를 다시 보냅니다.
- 요청에 `"mode": "onboarding"`을 넣으면 상담 대신 아직 이야기하지 않은 역량 영역을 하나씩 묻는 온보딩
  질문으로 답합니다(기본 `"counsel"`).
- 후보는 활성 목표 직무의 승인된 요구 역량 중 아직 보유하지 않은 것만, 사용자 발화를 글자 그대로
  인용한 근거와 함께 나옵니다. **자동 저장하지 않습니다.**
- 사용자가 확인하면 `POST /api/v1/me/capabilities`로 실제 분류(`skill`, `certification`, `test`,
  `project`, `experience`)와 `raw_text: label`, 명시적으로 선택한 `proficiency`,
  `details: {"source": "LLM_ASSISTED", "competency_ids": [entity_id], "evidence": evidence_quote}`를
  저장합니다. 인정에는 20자 이상의 구체적 근거가 필요하므로 인용이 짧으면 사용자가 사실에 맞게
  보충해야 합니다. 자격/시험의 발급·만료일도 확인합니다. 후보나 제목만으로는 인정되지 않습니다.
- 후보 제외는 분석과 같은 활성 상태·확인 역량 ID·근거·경험 수준·유효기간 규칙에 따른 완전 인정
  역량에만 적용합니다. 부분 인정 기록은 보완할 수 있습니다. 인용은 대소문자·공백·문장부호까지
  사용자 발화의 정확한 부분 문자열이어야 합니다.
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
| `JOBTOLOGY_AUTH_ENABLED` | Google 로그인 활성화 여부. 운영은 `true`와 Google OAuth 설정을 사용합니다. 비활성 상태의 제품 라우트는 fail-closed `401`을 반환 |
| `JOBTOLOGY_ENVIRONMENT` | `development` / `test` / `production` |

환경 변수 전체 목록과 최신 기본값은 `.env.example` 과
[settings.py](../src/jobtology_be/settings.py) 를 참고하세요.

### 프로필·역량·기본 경로 조건

- `PUT /api/v1/me/profile`은 전체 필드를 대체합니다. `major_raw: ""`는 전공 미입력/초기화를
  뜻하며, 선택 필드는 `null`로 지울 수 있습니다. 표시용 `미입력`을 실제 전공으로 저장하지 않습니다.
  FE의 졸업 연도/2월·8월 선택은 해당 월 말일로 전달하며, 기존 날짜는 사용자가 변경하지 않으면 보존합니다.
- 역량 생성/수정/삭제 응답은 식별자와 `profile_version`입니다. 생성 응답을 전체 역량 상세로
  간주하지 마세요. 생성된 내용을 즉시 표시하거나 상세/목록을 다시 읽고, 이후 쓰기는 최신 버전을 사용합니다.
- `GET /api/v1/me/capabilities`와 단건 GET은 소유자의 `ACTIVE` 역량만 반환합니다.
  삭제는 `REVOKED` 처리이며, 삭제된 단건 조회는 `404`입니다. 분석 입력에서도 제외됩니다.
- 기존 계정에 경로 조건 행이 없으면 `GET /api/v1/me/route-preferences`는 `200`과 분석 기본값을
  반환합니다: 주당 10시간, `budget_mode=REGULAR`, 예산 상한 `null`, 세 가지 선호 `false`,
  `availability_source=DEFAULT`, 현재 `profile_version`. 읽기는 행을 만들거나 버전을 올리지 않습니다.
  프로필이 없는 사용자는 계속 `404`입니다.
- 프로필/역량/선호 변경은 현재 분석을 갱신하기 위한 재계산을 예약하지만 기존 활성 로드맵을
  덮어쓰지 않습니다. 임의의 스펙 문장은 카탈로그와 연결되지 않으면 미확인으로 남을 수 있습니다.

### 그래프 노드에서 실제 자료 열기

`GET /api/v2/roles/{occupation_id}/evidence-graph`의 노드는 선택적 구조화 필드를 제공합니다.
`ncs_category`는 NCS 직무/공고 집계 노드의 공고 목록 필터, `ncs_base_code`는 능력단위 탐색,
`qualification_code`는 자격 시험 일정 조회에 사용합니다. `requirement_key`는 현재 승인된 직무
메타데이터와 일치할 때만 제공되며, 원본 그래프 노드라는 이유만으로 분석 요구사항으로 간주하지 않습니다.
공고 집계 노드 ID는 실제 개별 공고 ID가 아닙니다. 먼저 필터된 공고 목록을 조회하고 실제
`posting_id`로 상세를 여세요. 원본 ID나 표시 문자열을 분해해 제품 요구사항을 추정하지 마세요.

NCS 1–8수준 설명은 [공식 수준체계](https://www.ncs.go.kr/mobile/rm01/TH10200103.do)를
참고합니다. 학력/자격의 대략적인 비유는 응시 자격, 학력 동등성, 개인 숙련도 판정이 아닙니다.

전체 기능 제한과 다음 작업은 [제품 기능 현황](plan.md#verified-functionality-limits-2026-10-20)을 참고하세요.

## 문서 링크 모음

- 대화형 API 문서(Swagger UI): [\/api\/docs](/api/docs)
- 열람용 API 문서(ReDoc): [\/api\/redoc](/api/redoc)
- OpenAPI JSON: [\/api\/openapi.json](/api/openapi.json)
- 이 문서의 원본 markdown: 저장소 `docs/fe-integration.md`

### Résumé scanner

Onboarding and My Info share **내 스펙 → 이력서에서 가져오기**. The authenticated status, raw-binary scan, and explicit selected-passage interpretation endpoints are documented in [resume-scanner.md](resume-scanner.md). A scan does not mutate the profile or call AI. Review each draft in the existing spec editor, require an explicit experience-level choice, then save with ordinary capability versioning/idempotency and full readback. Imported specs use `DOCUMENT_IMPORT` plus typed source quotation/span metadata. No SQL migration or new secret is required; deploy the BE first. Original files are request-only temporary data, and OCR/automatic education-profile writes are not implemented.
