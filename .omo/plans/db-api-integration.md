# db-api-integration - Work Plan

## TL;DR (For humans)
**What you'll get:** 수집된 여섯 공식 소스를 버전별 서비스 데이터로 만들고, 직업·역량·공고·기관·자격·시험 일정·경력 경로를 조회하게 한다. 검토된 근거가 갖춰지면 기존 분석·로드맵 계산에 연결하고, 데이터 갱신·만료·철회를 사용자 결과에 안전하게 반영한다.

**Why this approach:** 원본 수집과 사용자 서비스 사이에 검증된 게시 단계를 둔다. 한 결과가 여러 버전의 사실을 섞지 않게 하고, 기존 기능을 깨지 않는 추가 API와 입력 어댑터로 확장한다.

**What it will NOT do:** 로그인·프런트엔드·사람인·Work24 연동은 추가하지 않는다. 없는 교육과정이나 검토되지 않은 요구사항을 만들어 실제 데이터처럼 제공하지 않는다. 지금은 계획만 작성하며 운영 시스템은 변경하지 않는다.

**Effort:** XL — 두 저장소, 데이터 생산부터 제품 결과 유효성까지 포함한다.
**Risk:** High — 소스 의미 매핑, 검토 자료 확보, 두 저장소 게시 일관성과 철회 처리.
**Decisions to sanity-check:** 기존 8개 소스 MVP와 구분되는 6개 공식 소스 프로필을 사용한다. 실제 검토 자료가 없으면 개발 검증은 가능해도 운영 분석 활성화는 차단한다.

Your next move: 계획 검토 후 별도 구현 세션에서 실행을 승인한다. 이 문서의 커밋·푸시는 구현 착수나 운영 배포 승인이 아니다.

---

> TL;DR (machine): XL / High / DB 정규화·게시 + 공식 데이터 API + 네이티브 분석·계획 + 유효성·근거 조회. 계획만 작성하며 구현은 별도 승인 후 실행한다.

## Scope
### Must have
- 사용자 선택 범위: 이전 검토의 **2. DB→BE 계약/게시 파이프라인, 3. 수집 데이터 조회 API, 4. 분석·경로 계산 연결, 5. 철회·만료·갱신·근거 조회** 전부.
- 이 문서가 두 저장소의 단일 실행 계획이다. `DB/`는 형제 저장소 `Jobtology-DB/`, `BE/`는 이 저장소 `Jobtology-BE/`를 뜻한다. 이후 경로는 이 접두어 기준이다. `신규` 표시는 현재 존재하지 않는 구현 대상이다.
- 현재 근거: DB `README.md`, `docs/collection-status.md:28-71`, `docs/implementation-plan.md:419-427,1757`; BE `corpus/source_factory.py:100-177`(이하 코드 경로는 `src/jobtology_be/` 기준), `docs/neo4j-source-contract.md:164-196`, `api/neo4j_catalog.py:73-158`.
- 현재 DB는 FETCHED까지, BE는 local JSON M1–M5와 제한된 네이티브 카탈로그까지 구현되어 있다. 수집 현황은 2026-09-06 문서 기록이며 현재 운영 데이터의 검증 결과가 아니다.
- 소스는 정확히 `ncs_competency`, `ncs_qualification`, `ncs_career_path`, `qnet_schedule`, `alio_organization`, `job_alio`. 기존 8-source `MVP`를 변경하지 않고 **`OFFICIAL_SIX_SOURCE_V1`** 프로필을 새 ADR와 계약 버전으로 추가한다. 모든 응답/분석 메타데이터에 이 제한을 표시한다.
- 운영 분석은 추가로 검토된 `INTERNAL_EDITORIAL` revision을 요구한다. 실제 요구사항·별칭·경험 코드·활동 템플릿 작성/검토 자료가 없으면 운영 활성화는 BLOCKED다. 구현 완료와 운영 준비 완료를 별도 판정한다.

### 고정 설계 결정
1. DB는 수집·정규화·원천 근거·릴리스·그래프 projection을 소유한다. BE는 사용자 상태·조회 API·분석·계산·결과 유효성을 소유한다. DB에 새 공개 HTTP 서버는 만들지 않는다.
2. DB loader는 기존 DB 설계대로 공식 Neo4j Python driver를 사용한다(현재 미설치이므로 DB 의존성/lock 추가 작업 포함). BE는 기존 Query API 고정 쿼리 클라이언트를 재사용한다. 임의 Cypher API는 금지한다.
3. PostgreSQL 제한 조회 뷰 `published_release_states`, `active_corpus_releases`(신규)를 authoritative lifecycle로 둔다. BE용 별도 읽기 전용 연결/역할을 만들고 원본 ledger/base table 접근을 금지한다. API 설정 후보는 `JOBTOLOGY_CORPUS_DATABASE_URL`, `JOBTOLOGY_ENABLE_NATIVE_EDITORIAL=false`로 고정하여 문서화한다.
4. DB 릴리스 상태는 `PREPARING → READY → ACTIVE → SUPERSEDED`, 어느 게시 상태에서든 철회 시 `REVOKED`다. BE의 기존 `PUBLISHED/UNPUBLISHED/REVOKED/FIXTURE` enum은 유지한다. authoritative ACTIVE/SUPERSEDED + 검증된 editorial revision만 PUBLISHED로 매핑한다. 소스 native `READY`는 절대 이 매핑 대상이 아니다.
5. Graph를 release별로 준비·검증한 뒤 PostgreSQL pointer를 CAS transaction으로 바꾼다. 이는 **두 DB 간 분산 트랜잭션이 아니다**. crash recovery와 재시도/reconcile을 명시적으로 구현한다. 새 요청은 active ID를 한 번 선택하고 기존 결과는 저장된 release ID를 끝까지 사용한다.
6. identity: source entity는 `(source_id, source_record_id)`, revision은 canonical payload hash, release는 manifest hash에 연결된 불변 ID다. BE occupation ID와 NCS source ID는 검토된 매핑 파일로 연결한다. basis version은 contract/methodology/editorial revision을 포함한다. API cursor는 release ID+정렬키를 포함하고 다른 필터/release와 섞이면 거절한다.
7. 기존 `/api/v1` 제품 API와 `/api/v2/occupations`, `/publications`, `/publications/{id}/alignments`는 유지한다. 새 정규화 카탈로그는 `/api/v2/catalog/*`로 추가하고 분석은 기존 v1 서비스와 worker를 재사용한다. source/profile은 별도 필드이며 기존 `basis_type`의 의미를 바꾸지 않는다.
8. 학습 활동은 검토된 editorial 활동/자격시험 기반만 허용한다. 실제 교육과정·수강 신청·교육비를 Work24 없이 생성하지 않는다. 알려지지 않은 비용은 기존 UnknownKrwCost로 유지하고 일정이 없으면 임의 날짜를 넣지 않는다.
9. 권리·철회는 모든 source-backed read와 mutation 직전에 조회한다. authoritative DB 장애 시 503으로 차단한다. worker는 실행 전과 결과 commit 전 재검증한다. revoke가 결과 commit과 경합해도 새 결과 조회는 차단되며 poller가 정합성을 복구한다.
10. 변경 감지 poller 주기는 60초, 작업은 `(event_id, artifact_id)` unique key로 중복 방지한다. 철회된 DRAFT/ACTIVE 로드맵은 INVALIDATED, 사용자 입력은 보존한다. 새 유효 release가 있을 때만 재계산하고 자동 로드맵 생성/활성화는 하지 않는다.

### 새 API 계약(계획안; 현재 구현되어 있다는 뜻이 아님)
모두 인증 필요. 목록은 `{items, next_cursor, release_id, data_as_of, source_profile}` envelope, `limit=20` 기본/최대 100, 고정 ID 정렬을 사용한다. `release_id` 생략 시 첫 페이지에 active를 pin한다. 상세도 동일한 메타데이터를 반환한다. 필터는 AND, `q`는 정규화된 이름/제목의 parameterized case-insensitive 검색으로 정의하며 벡터 검색은 하지 않는다.

| 경로(GET, `/api/v2/catalog` 접두어) | 필터/관계 | 공개 필드(공통 id/revision/evidence_ids 외) |
| --- | --- | --- |
| `/occupations`, `/occupations/{id}` | q, parent_id | name, code, parent_id, aliases, children 링크 |
| `/competencies`, `/competencies/{id}` | q, occupation_id | name, full_versioned_ncs_code, classification |
| `/organizations`, `/organizations/{id}` | q | name, source_url, source 제공 지역 |
| `/postings`, `/postings/{id}` | q, organization_id, occupation_id, state | title, organization_id, location, employment_type, published_at, valid_through, source_url, serving_state; 없는 값은 null |
| `/qualifications`, `/qualifications/{id}` | q, competency_id | name, issuer, qualification_code, competency_ids |
| `/exam-sessions`, `/exam-sessions/{id}` | qualification_id, from, to | qualification_id, registration_start/end, exam_start/end, timezone, source_url |
| `/career-paths`, `/career-paths/{id}` | occupation_id | source job code, 단계/관계, 관련 competency IDs; 승진 보장 등 추론 금지 |
| `/evidence/{id}` | release_id | source_id, source_url, observed_at, locator, rights-permitted excerpt 또는 withheld 사유 |
| `/releases/{id}` | 없음 | state, source_profile, data_as_of, contract_version, availability 및 사유 |

- 오류: 인증 없음 401, 존재하지 않는 항목 404, 잘못된 필터/cursor 422, 철회된 release 410 `CORPUS_RELEASE_REVOKED`, 소스/권한/계약 준비 실패 503(기존 오류 envelope 재사용). 검증된 빈 검색은 200 빈 배열; 소스 미준비를 빈 검색으로 위장하지 않는다.
- `from/to`는 timezone-aware RFC3339, 교차 구간 포함 조건으로 필터한다. 일자-only 원본은 KST 달력일과 정밀도 필드를 보존하고 시간 정밀도를 꾸미지 않는다. 만료된 공고/시험은 historical 조회 가능하되 현재 추천에는 제외한다.
- 구현 시 응답 타입·필터·필드별 nullable/권리 정책을 계약 JSON Schema에 고정하고 OpenAPI snapshot으로 검증한다.

### Must NOT have (guardrails, anti-slop, scope boundaries)
- Google 로그인, 인증 우회, 프런트엔드, 채팅, 지원/수강 자동화, 시장예측, 사람인/Work24 활성화, 벡터 검색은 제외한다.
- 사용자 DB와 ingestion DB 통합, existing endpoint breaking change, local JSON 자동 fallback, 원문/첨부 바이트/개인정보/비밀키 공개는 금지한다.
- 운영 데이터 purge·운영 migration·실제 배포는 이 계획의 개발 실행으로 자동 승인되지 않는다. 폐기 가능한 환경에서만 재현하고 운영은 별도 승인한다.
- 기존 `.gitignore` 변경, `.omo/evidence/`, `.omo/run-continuation/` 등 무관한 변경을 수정·커밋하지 않는다. 이번 요청은 이 계획 파일의 커밋·푸시만 수행한다.

## Verification strategy
> 기술 검증은 agent-executed. 실제 editorial/권리 검토의 승인을 자동 테스트로 대체하지 않는다.
- Test decision: **TDD / pytest**. 각 작업에서 실패 재현 → 구현 → regression. 실제 소스 호출/운영 DB 접속은 불필요하다.
- Evidence: `.omo/evidence/db-api-integration/task-N.txt` 및 `final-FN.txt`(커밋 제외). 실행 명령/exit code/테스트 수/skip 수/양쪽 SHA 기록. 수집 원문·개인정보·DSN은 기록하지 않는다.
- 아래 테스트 경로 중 신규 표시는 각 작업이 만들어야 하는 파일이다. 현존 테스트인 것처럼 실행 결과를 주장하지 않는다. 명령은 표시된 DB 또는 BE 작업 디렉터리에서 실행한다.
- 공통 gate(DB): `uv run pytest`, `uv run ruff check .`, `uv run pyright`, `uv lock --check`, `uv build`.
- 공통 gate(BE): `uv run pytest`, `uv run ruff check .`, `uv lock --check`, `uv build`. BE에는 pyright 개발 의존성이 없으므로 설치되지 않은 명령을 gate로 꾸미지 않는다.
- 신규 `BE/tests/integration/conftest.py`는 `JOBTOLOGY_INTEGRATION_REQUIRED=1`일 때 disposable services/session/DB CLI 누락을 skip이 아닌 failure로 처리한다. 기존 PostgreSQL 수용 테스트에는 `JOBTOLOGY_ACCEPTANCE_DATABASE_URL`을 공급한다.
- 신규 `BE/deploy/integration/compose.yaml`에서 loopback-only 임시 app PostgreSQL, corpus PostgreSQL, Neo4j를 고정 버전으로 기동한다. 운영 변수/볼륨을 재사용하지 않고 별도 project/volumes, disposable DB 이름 검증과 cleanup fixture를 둔다. 버전은 각 저장소 현재 배포 설정에서 가져와 계약 문서에 기록한다.
- 인증 성공 검증은 실제 disposable 세션 저장소를 통과한다. 테스트용 세션은 DB fixture로 발급하고 프로덕션 로그인/신뢰 헤더 endpoint를 추가하지 않는다. 비인증 401, 타 사용자 404/403, mutation CSRF 거절도 검증한다.

## Execution strategy
### Parallel execution waves
- W1 계약/테스트 기반: 1 → (2, 3). 동시 변경 충돌이 없도록 두 저장소별 소유권을 나눈다.
- W2 데이터 생산: (4, 5, 6, 7) → 8. parser별 병렬; quality gate는 모두 완료 후.
- W3 게시/서빙: 9 → 10 → (11, 19). 19는 BE가 아니라 DB lifecycle 소유 작업이다.
- W4 BE API/스냅샷: (12, 13, 14, 15, 16). 공통 router wiring은 한 명이 통합한다.
- W5 제품/유효성: (17, 20) → 18. 20은 19 완료에 의존하며 18은 17과 20 모두 완료 후 검증한다.
- W6 최종: 21 → 22 → F1–F4 병렬. 작은 선행 wave는 schema/publication의 실제 의존성 때문에 분리했다.
- 각 구현 작업은 Python `programming` skill을 적용하고, 공통 계약/마이그레이션/설정 파일의 동시 편집을 금지한다. 저장소별 task-owned branch/worktree를 사용한다.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | 없음 | 2,3 | 없음 |
| 2,3 | 1 | 4–7 | 서로 |
| 4,5,6,7 | 2,3 | 8 | 서로(parser/editorial 파일 분리) |
| 8 | 4–7 | 9 | 없음 |
| 9 | 8 | 10 | 없음 |
| 10 | 9 | 11,19 | 없음 |
| 11 | 10 | 12–16 | 19 |
| 12,13,14,15,16 | 11 | 17,20,21 | 서로(공통 파일 단일 통합) |
| 19 | 10 | 20 | 11–16 |
| 17 | 16 | 18 | 20 |
| 20 | 15,16,19 | 18,21 | 17 |
| 18 | 17,20 | 21 | 없음 |
| 21 | 12–15,18,20 | 22 | 없음 |
| 22 | 21 | F1–F4 | 없음 |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [ ] 1. 양쪽 저장소 계약·6-source 프로필 ADR 확정
  - 대상: DB 신규 `docs/decisions/0002-official-six-source-publication.md`, `contracts/publication-v1/`; BE 신규 `docs/db-publication-contract.md`, `tests/fixtures/publication-v1/`. producer JSON Schema와 positive/negative fixtures를 DB에 두고 BE copy의 SHA 일치를 테스트한다.
  - 구현: 위 고정 결정/API를 필드 단위 계약화; source/profile/release/basis/rights/evidence IDs와 supported version을 고정. manifest에 여섯 run IDs, pipeline/methodology/editorial revision, validation hashes, data_as_of, per-kind count/hash를 포함한다. 기존 8-source MVP와 새 프로필을 구분한다.
  - 참조: DB `docs/implementation-plan.md:419-427,552,1757`; BE `docs/neo4j-source-contract.md:164-196`, `src/jobtology_be/modules/analyses/editorial_models.py:18-22,124-187`.
  - 검증: 신규 DB `tests/unit/test_publication_contract.py`, BE `tests/test_publication_contract.py`; 각 repo에서 `uv run pytest <해당 파일>`.
  - QA: happy=6 sources+review metadata 계약 통과; failure=누락/추가 source, READY→PUBLISHED 오인, unknown contract version 거절. Evidence task-1.txt.
  - Commit: 각 repo 별 `Define official six-source publication contract`. Depends: 없음.

- [ ] 2. 폐기 가능한 교차 저장소 테스트 환경 준비
  - 대상: BE 신규 `deploy/integration/compose.yaml`, `tests/integration/conftest.py`, `tests/integration/test_environment.py`; DB 신규 `tests/fixtures/official-six/`, `tests/integration/conftest.py`.
  - 구현: six-source synthetic raw fixtures를 manifest/ledger와 함께 적재; corpus와 app DB 분리, fresh migrations, Neo4j readiness, 실제 세션 fixture. 새 driver 의존성은 DB lock에 고정한다. fixture는 명시적 TEST provenance이며 production flag가 있으면 거절한다.
  - 참조: DB `deploy/dev/compose.yaml`, `migrations/`; BE `tests/test_acceptance_m5_lifecycle.py:24-46`, `pyproject.toml`.
  - 검증: `docker compose -p jobtology-integration -f deploy/integration/compose.yaml up -d --wait`; BE `JOBTOLOGY_INTEGRATION_REQUIRED=1 uv run pytest tests/integration/test_environment.py`.
  - QA: happy=양 DB migration/graph/session roundtrip; failure=누락 service, non-disposable DB name, missing DB CLI 모두 failure이고 skip 아님. Evidence task-2.txt. 테스트에서 사용할 두 repo의 venv/CLI 경로도 기록한다.
  - Commit: 각 repo 별 `Add disposable publication integration fixtures`. Depends: 1.

- [ ] 3. DB 정규화·근거·release 영속 모델 추가
  - 대상: DB 신규 `src/jobtology_db/contracts/normalized.py`, `storage/normalized.py`, `storage/releases.py`, `migrations/versions/0004_normalized_publications.py`, `tests/integration/test_normalized_storage.py`.
  - 구현: normalized revisions, observations, evidence locators, quarantine, stage checkpoints, release members/manifest, lifecycle events, active pointer 스키마. raw store는 그대로 둔다. stage idempotency=(run_id,input_hash,pipeline_version,stage), entity revision은 source identity+canonical content hash. 잔존 run accounting 검증 가능하게 저장한다.
  - 참조: DB `migrations/versions/0001_fetch_ledger.py`, `0003_rights_policy_binding.py`, `src/jobtology_db/storage/`, `docs/implementation-plan.md:425-427`.
  - 검증: DB `uv run pytest tests/integration/test_normalized_storage.py`.
  - QA: happy=migrate/replay해 중복 0; failure=duplicate identity/FK mismatch rollback, 기존 fetch ledger 보존. Evidence task-3.txt.
  - Commit: `Persist normalized publication artifacts`. Depends: 1.

- [ ] 4. NCS 역량·자격 매핑 parser와 resolver 구현
  - 대상: DB 신규 `src/jobtology_db/pipeline/parsers/ncs.py`, `pipeline/normalize/ncs.py`, `tests/unit/test_normalize_ncs.py`.
  - 구현: 전체 versioned competency code 보존; 자격 매핑 identity에 `(ncsClCd,jmCd,organStdVerCd)` 포함. 분류/역량/자격 revision 생성, 다의적 매핑 quarantine. 원천 locator와 observation hash 연결.
  - 참조: DB `src/jobtology_db/connectors/sources.py`, `docs/collection-status.md:46-61`.
  - 검증: DB `uv run pytest tests/unit/test_normalize_ncs.py`.
  - QA: happy=동일 입력 동일 revision/evidence; failure=중복 페이지, version 누락, 모호한 mapping 거절. Evidence task-4.txt.
  - Commit: `Normalize NCS competencies and qualifications`. Depends: 2,3.

- [ ] 5. Q-Net 일정·NCS 경력 경로 정규화
  - 대상: DB 신규 `pipeline/parsers/qnet.py`, `pipeline/parsers/career_paths.py`, `pipeline/normalize/schedules.py`, `tests/unit/test_normalize_schedules.py`(`pipeline/` 앞은 `src/jobtology_db/`).
  - 구현: 응답에 없는 qualification identity는 검증된 request partition에서 복원; KST 날짜 정밀도/접수·시험 구간 보존; 원천 직무/경력 단계 관계 유지. 역량이나 승진 추천으로 자동 해석하지 않는다.
  - 참조: DB `src/jobtology_db/partition_config.py`, `docs/collection-status.md:46-61`, `connectors/sources.py`.
  - 검증: DB `uv run pytest tests/unit/test_normalize_schedules.py`.
  - QA: happy=partition-qualified exam/path 연결; failure=partition 누락, 역전 날짜, 없는 target ID quarantine. Evidence task-5.txt.
  - Commit: `Normalize exam schedules and career paths`. Depends: 2,3.

- [ ] 6. ALIO 기관·JOB-ALIO 공고 정규화
  - 대상: DB 신규 `pipeline/parsers/alio.py`, `pipeline/normalize/postings.py`, `tests/unit/test_normalize_postings.py`.
  - 구현: `recrutPblntSn` 유지, 기관과 명시적 매핑, 목록/상세 completeness 검증. 첨부 metadata만 허용. source state, valid-through, consecutive absence와 release별 observation state 분리. 불완전 run의 누락을 폐업/마감으로 해석하지 않는다.
  - 참조: DB `connectors/sources.py`, `docs/implementation-plan.md:770-783`, `docs/collection-status.md:18-19`.
  - 검증: DB `uv run pytest tests/unit/test_normalize_postings.py`.
  - QA: happy=기관+공고 연결/명시 마감; failure=detail 실패 시 이전 상태 보존, 두 번 full-run 부재만 NOT_SEEN, 첨부 bytes 제외. Evidence task-6.txt.
  - Commit: `Normalize official organizations and postings`. Depends: 2,3.

- [ ] 7. 검토된 editorial corpus import/검증 경로 구현
  - 대상: DB 신규 `src/jobtology_db/editorial/`, `config/editorial/README.md`, `tests/unit/test_editorial_import.py`; 계약 스키마는 작업 1 확장.
  - 구현: 문서의 기존 네 canonical target occupations에 대해 source→app ID mapping, alias, experience code, requirement necessity/support refs, activity revision/effort/cost/prerequisite/outcome/completion criteria를 검증. reviewed_at/reviewer 승인 식별자/Git revision과 fixture flag 필수. 실제 제공된 검토 자료가 있으면 import하고, 없으면 운영 활성화 차단 사유를 출력한다. 리뷰어 개인정보는 공개하지 않는다.
  - 참조: BE `corpus/local_snapshot.py:29-79`, `modules/analyses/editorial_models.py:124-187`, `planning/candidate_models.py`; DB `docs/implementation-plan.md:10-17,783`.
  - 검증: DB `uv run pytest tests/unit/test_editorial_import.py`.
  - QA: happy=synthetic reviewed fixture를 TEST namespace로 import; failure=근거 없음/순환 DAG/unknown outcome/fixture의 운영 승격/허위 training course 거절. Evidence task-7.txt.
  - Commit: `Validate reviewed editorial publication inputs`. Depends: 2,3.

- [ ] 8. 파이프라인 실행·권리·품질·freshness gate 연결
  - 대상: DB `src/jobtology_db/cli.py`, 신규 `pipeline/processing.py`, `pipeline/quality.py`, `tests/integration/test_processing_pipeline.py`.
  - 구현: CLI `jobtology process run <run-id>`를 추가해 parse→normalize→evidence→resolve→rights/quality/checkpoint를 실행한다. 성공할 때만 connector run SUCCEEDED. 거절/누락/제외 건수 accounting과 observation/validation hash를 검증한다. source freshness 기준은 기존 계획의 해당 여섯 source 기준을 재사용하고 새 profile ADR에 표로 고정한다. 미지원 두 source의 성공을 가장하지 않는다.
  - 참조: DB `cli.py:118-185`, `src/jobtology_db/rights.py`, `docs/implementation-plan.md:419-427,770-783`.
  - 검증: DB `uv run pytest tests/integration/test_processing_pipeline.py`.
  - QA: happy=6 fixtures 처리/재실행 동일 결과; failure=hash 변조, missing page, rights 만료, stale source가 publish 후보에 못 들어감. Evidence task-8.txt.
  - Commit: `Process fetched sources through publication gates`. Depends: 4,5,6,7.

- [ ] 9. Neo4j release-scoped projection loader 구현
  - 대상: DB 신규 `src/jobtology_db/publication/graph_loader.py`, `publication/queries/`, `tests/integration/test_graph_publication.py`.
  - 구현: canonical identity shells + immutable revision/assertion + release INCLUDES edges. 각 batch는 parameterized bounded write; source/contract 별 deterministic keys, retries idempotent. count/membership hash 검증 후만 READY. 인덱스와 constraints 포함. 기존 native reviewedNcsPublication label을 덮어쓰지 않는다.
  - 참조: DB `docs/implementation-plan.md:425-427,504,1497`; BE `corpus/neo4j_queries.py`, `docs/neo4j-source-contract.md`.
  - 검증: DB `uv run pytest tests/integration/test_graph_publication.py`.
  - QA: happy=R1/R2 공존, R1은 옛 title/date 반환; failure=중간 batch crash 후 replay, hash/count mismatch READY 불가. Evidence task-9.txt.
  - Commit: `Load immutable release-scoped graph projections`. Depends: 8.

- [ ] 10. 릴리스 활성화·lifecycle 조회 뷰·복구 CLI 구현
  - 대상: DB 신규 `publication/releases.py`, `publication/reconcile.py`, migration lifecycle views/roles, `tests/integration/test_release_activation.py`; `cli.py`에 `publication prepare/activate/status/reconcile` 추가.
  - 구현: profile별 active pointer, CAS activation, authoritative state/events transaction. graph READY+검증 manifest 없으면 활성화 금지. 카탈로그는 여섯 source가 충족되면 게시 가능하되 editorial 없는 release는 catalog-only capability. graph READY 후 crash는 pointer 유지; commit 후 graph ACTIVE marker 실패는 PG 권위로 idempotent reconcile. SUPERSEDED non-revoked pinned read 지원.
  - 참조: DB `docs/implementation-plan.md:427,552`; BE `modules/analyses/editorial_models.py:18`.
  - 검증: DB `uv run pytest tests/integration/test_release_activation.py`.
  - QA: happy=R1→R2 atomic PG pointer 전환; failure=동시 activate 한 건만 성공, 각 crash window에서 old/validated new만 보임, BE 역할 base-table SELECT 거절. Evidence task-10.txt.
  - Commit: `Activate verified corpus releases safely`. Depends: 9.

- [ ] 11. BE release reader·고정 쿼리·오류 계약 구성
  - 대상: BE 신규 `corpus/publication_contracts.py`, `corpus/release_reader.py`, `corpus/published_repository.py`; `settings.py`, `main.py`, 신규 `tests/test_published_repository.py`.
  - 구현: 제한 PG lifecycle connection과 기존 Neo4j Query API 연결; source state와 release state 구분; 한 요청/커서 release pinning; response field allowlist/limits. catalog/source capabilities를 실제 검증 metadata로 산출한다. global enable flag만으로 AVAILABLE 처리 금지.
  - 참조: BE `corpus/neo4j_client.py`, `neo4j_queries.py`, `neo4j_repository.py`, `source_factory.py`, `api/errors.py`.
  - 검증: BE `uv run pytest tests/test_published_repository.py tests/test_neo4j_catalog_api.py tests/test_configured_corpus_source.py`.
  - QA: happy=검증된 pinned 조회; failure=PG down/unknown contract/graph mismatch/revoked가 typed 503/410, local JSON fallback 없음. Evidence task-11.txt.
  - Commit: `Read verified native corpus publications`. Depends: 10.

- [ ] 12. 직업·역량 카탈로그 목록/상세 API 구현
  - 대상: BE 신규 `api/catalog/occupations.py`, `api/catalog/competencies.py`, `api/catalog/models.py`, `tests/test_published_occupations_api.py`.
  - 구현: 위 `/catalog/occupations`, `/competencies` 계약, q/parent/occupation 필터, 정규화 ID/계층/alias/evidence metadata; 기존 source-native API는 그대로 둔다.
  - 참조: BE `api/neo4j_catalog.py:73-94`, `api/occupation_source_models.py`, 작업 1 계약.
  - 검증: BE `uv run pytest tests/test_published_occupations_api.py tests/test_neo4j_catalog_api.py`.
  - QA: happy=계층/역량 상세·페이지 연결; failure=알 수 없는 ID 404, 위조 cursor/limit 422, 인증 없음 401. Evidence task-12.txt.
  - Commit: `Expose published occupations and competencies`. Depends: 11.

- [ ] 13. 기관·공고 조회/검색 API 구현
  - 대상: BE 신규 `api/catalog/organizations.py`, `api/catalog/postings.py`, `tests/test_published_postings_api.py`.
  - 구현: 위 기관/공고 계약; 필터별 query allowlist, source state 표시, 만료 표시, 기관 연결. source missing 값 null, 검색 파라미터 interpolation 금지. 원문/첨부 bodies 제외.
  - 참조: BE `corpus/neo4j_queries.py`, 작업 6 normalized contract, 작업 11 repository.
  - 검증: BE `uv run pytest tests/test_published_postings_api.py`.
  - QA: happy=기관별 공고/한글 q/마감 필터/pagination; failure=다른 release cursor 거절, 악성 q를 literal로 처리, prohibited response fields 0. Evidence task-13.txt.
  - Commit: `Expose official organizations and job postings`. Depends: 11.

- [ ] 14. 자격·시험 일정·경력 경로 API 구현
  - 대상: BE 신규 `api/catalog/qualifications.py`, `api/catalog/exams.py`, `api/catalog/career_paths.py`, `tests/test_published_qualifications_api.py`.
  - 구현: 위 계약과 NCS qualification relations, partition-qualified exam identity, timezone/precision. route computation과 단순 source career path 조회를 구분한다.
  - 참조: DB `docs/collection-status.md:46-61`, 작업 4/5 contracts; BE `api/neo4j_catalog.py` 패턴.
  - 검증: BE `uv run pytest tests/test_published_qualifications_api.py`.
  - QA: happy=역량→자격→시험/경력 관계; failure=역전 기간/naive time 422, 근거 없는 일정이나 training 레코드 생성 없음. Evidence task-14.txt.
  - Commit: `Expose qualifications exams and career paths`. Depends: 11.

- [ ] 15. 근거·release API와 공개 권리 정책 구현
  - 대상: BE 신규 `api/catalog/evidence.py`, `api/catalog/releases.py`, `corpus/evidence_policy.py`, `tests/test_published_evidence_api.py`.
  - 구현: immutable locator+관찰 metadata 기반 근거 조회. 정책이 허용한 excerpt만 반환; 원문 대신 withheld 사유 가능. 개인/리뷰어/candidate/secret/raw bytes 차단. release inspection도 revoked source content를 반환하지 않는다. user shell은 기존 소유권 검사 통과한 제품 endpoint에서만 제공한다.
  - 참조: BE `docs/neo4j-source-contract.md:185-196`, DB `src/jobtology_db/rights.py`, `docs/implementation-plan.md:268,1463`.
  - 검증: BE `uv run pytest tests/test_published_evidence_api.py`.
  - QA: happy=허용 excerpt 또는 safe withheld; failure=revoked 410, cross-release evidence 404, 민감정보 canary가 응답/로그에 없음. Evidence task-15.txt.
  - Commit: `Expose rights-aware evidence and release metadata`. Depends: 11.

- [ ] 16. Native snapshot adapter와 저장된 source identity 확장
  - 대상: BE 신규 `corpus/published_snapshot.py`, `tests/test_native_snapshot.py`, 신규 persistence migration; `corpus/snapshot.py`, `source_factory.py`, `application/services/analysis_inputs.py`, recompute context/trace/proposal metadata.
  - 구현: snapshot의 requirements/templates/aliases/experience를 동일 release에서 조립; exact selection 검사. source/profile/contract/data_as_of를 context·analysis·proposal·trace에 보존한다. legacy는 명시적 LEGACY_LOCAL_JSON origin으로 backfill하고 source validity를 새 upstream 사실로 추정하지 않는다. feature flag 기본 false.
  - 참조: BE `corpus/local_snapshot.py:71-145`, `corpus/snapshot.py`, `infrastructure/persistence/schema.py`, `workers/recompute.py:97-103`.
  - 검증: BE `uv run pytest tests/test_native_snapshot.py tests/test_corpus_snapshot.py tests/test_analysis_context_factory.py`; DB migration roundtrip 포함.
  - QA: happy=완전 editorial native snapshot/기존 context 읽기; failure=identity mismatch, missing template evidence, fixture 운영 로딩 거절. Evidence task-16.txt.
  - Commit: `Build source-pinned native editorial snapshots`. Depends: 11.

- [ ] 17. 기존 분석 API·worker에 native 경로 연결
  - 대상: BE `main.py`, `application/services/analyses.py`, `analysis_context.py`, `workers/recompute.py`, 신규 `tests/test_native_analysis.py`.
  - 구현: local-only wiring을 source-aware capability 조건으로 바꾸고 기존 normalizer/analyzer 재사용. native alignment accepted를 requirement로 승격하지 않는다. catalog-only와 incomplete editorial은 작업 접수 전 503, complete native만 enqueue. 저장 context를 현재 default 설정으로 재해석하지 않는다.
  - 참조: BE `api/analyses.py:121-196`, `main.py:172-180`, `workers/recompute.py`, `tests/test_neo4j_acceptance_api.py:108-164`.
  - 검증: BE `uv run pytest tests/test_native_analysis.py tests/test_analysis_context_factory.py tests/test_recompute_worker.py`.
  - QA: happy=POST→worker→분석 결과 source/profile pin; failure=incomplete 접수 시 DB/outbox 쓰기 0, 설정 전환 후 legacy job 정상, revoke-before-finalize 결과 차단. Evidence task-17.txt.
  - Commit: `Analyze verified native editorial publications`. Depends: 16.

- [ ] 18. 실제 자격 일정·editorial 활동을 경로 계산에 연결
  - 대상: BE 신규 `planning/published_availability.py`, `tests/test_native_planning.py`; `planning/candidates.py`, worker composition, trace metadata.
  - 구현: 기존 CandidateGenerator/CP-SAT 재사용; published activity duration/cost/prerequisites 및 qualifying exam/application windows를 CandidateAvailability로 연결. 목표 날짜 이후 일정/만료 공고 제외. missing evidence/비용/시간은 기존 typed behavior 유지. 생성물은 unsaved proposal이며 user activation 전 roadmap 생성 금지.
  - 참조: BE `planning/candidate_models.py`, `planning/candidates.py`, `planning/cp_sat_model.py`, `api/roadmaps.py`.
  - 검증: BE `uv run pytest tests/test_native_planning.py tests/test_recompute_worker.py`.
  - QA: happy=시간/비용/선수조건과 시험 기간 충족; failure=불가능 일정, unknown cost+budget, cyclic prerequisites, revoked source에서 허위 성공/자동 활성화 없음. Evidence task-18.txt.
  - Commit: `Plan routes from verified published activities`. Depends: 17,20.

- [ ] 19. DB 철회·만료·갱신 event와 권리 purge 구현
  - 대상: DB 신규 `publication/lifecycle.py`, `publication/purge.py`, `tests/integration/test_release_revocation.py`; CLI `publication revoke/refresh`, `rights purge --dry-run` 및 별도 명시 적용 옵션.
  - 구현: release status+event+active pointer 변경 transaction; revoked rollback 영구 금지. normal refresh는 새 release를 만들고 기존 non-revoked history 유지. freshness/expiry는 실행 시각 의존 규칙으로 별도 표기한다. rights purge는 publish 정지→tombstone→철회→graph/raw/evidence 삭제·mask→restore manifest 검사 순서로 재시도 가능하게 만든다. 실제 backup 저장소 적용은 이 작업에서 실행하지 않는다.
  - 참조: DB `docs/implementation-plan.md:268,396,427,770-783`, `src/jobtology_db/rights.py`.
  - 검증: DB `uv run pytest tests/integration/test_release_revocation.py`.
  - QA: happy=disposable rights purge와 새 clean release; failure=revoked activate/rollback 거절, 중단 후 replay, 영향받은 backup manifest 잔존 시 publish 재개 금지. Evidence task-19.txt.
  - Commit: `Enforce publication lifecycle and rights revocation`. Depends: 10.

- [ ] 20. BE 기존 결과 무효화·갱신 재계산 연결
  - 대상: BE 신규 `workers/corpus_lifecycle.py`, `infrastructure/persistence/corpus_invalidations.py`, `tests/integration/test_corpus_invalidation.py`; 기존 roadmap source validity, M5 reads, analysis/proposal/trace reads/mutations.
  - 구현: 매 60초 monotonic event cursor poll; app transaction에서 invalidation+idempotent recompute outbox. revoked 결과는 410, user shell/progress만 소유자 조회 허용. expiry는 새 추천/완료되지 않은 source-backed action 차단, 역사/사용자 완료 이력 삭제 금지. replacement가 없으면 기다리고 생기면 한번 재계산. 정상 갱신은 opt-in/기존 정책에 맞는 proposal만 생성하고 활성 로드맵은 자동 교체하지 않는다.
  - 참조: BE `infrastructure/persistence/roadmap_source_validity.py`, `m5_queries.py`, `store.py`, `workers/`; DB `docs/implementation-plan.md:950-954,1463`.
  - 검증: BE `JOBTOLOGY_INTEGRATION_REQUIRED=1 uv run pytest tests/integration/test_corpus_invalidation.py`.
  - QA: happy=revoke→즉시 read 거절→poll invalidation/recompute 1회; failure=event 재전달·worker crash·동시 사용자 수정·replacement 없음에도 중복/권한누출/자동 activate 0. Evidence task-20.txt.
  - Commit: `Invalidate revoked corpus artifacts safely`. Depends: 15,16,19.

- [ ] 21. 교차 저장소 raw→API→분석→철회 수용 테스트
  - 대상: BE 신규 `tests/integration/test_db_be_publication.py`, `test_native_product_lifecycle.py`; DB fixture/CLI는 실제 작업 8–10을 사용한다.
  - 구현: mocking 없이 synthetic raw ledger→DB CLI process/prepare/activate→Neo4j+PG→인증된 catalog→분석/worker/proposal→명시 roadmap 생성/활성화→진행 변경→revoke→safe shell까지 검증. 앱과 DB 테스트 suites를 각각 실행하고 commit SHA 조합 고정. 생산자 payload를 BE 테스트가 손으로 재생성하지 않는다.
  - 참조: 기존 BE `tests/test_acceptance_m5_lifecycle.py`, `tests/test_acceptance_m5_worker.py`, 작업 2 harness.
  - 검증: BE `JOBTOLOGY_INTEGRATION_REQUIRED=1 uv run pytest tests/integration/`; DB `uv run pytest tests/integration/`; 양쪽 공통 gate 실행. integration tests 0 skip 필수.
  - QA: happy=두 release replay/rollback-to-nonrevoked 일관성; failure=source stale, broken hash, partial graph, 권리 만료, missing editorial, TEST production 거절, revoked historical read. Evidence task-21.txt.
  - Commit: 각 repo 별 `Verify official publication product lifecycle`. Depends: 12,13,14,15,18,20.

- [ ] 22. 운영 절차·문서·단계별 rollout 정리
  - 대상: DB `README.md`, `docs/collection-status.md`, `docs/implementation-plan.md`, `deploy/`; BE `README.md`, `docs/fe-integration.md`, `docs/neo4j-source-contract.md`, `docs/goldship-deployment.md`.
  - 구현: 신규 CLI/env/제한 읽기 role/계약 버전/오류/refresh/poller/runbook. catalog-only → editorial 검토 자료 확보 → staging end-to-end → flag enable 순서. rollback은 BE flag off와 검증된 non-revoked release pointer만 허용. offline raw 처리와 실제 신규 수집은 분리. poll/refresh는 기존 배포 worker/scheduler에 통합하고 중복 scheduler 도입 금지.
  - 참조: 양 repo 배포 문서, task-21 evidence, 기존 source contract.
  - 검증: 양쪽 `uv build`; BE `uv run pytest tests/integration/test_environment.py`; 문서의 CLI `--help`와 OpenAPI path/오류 snapshot 교차검증.
  - QA: happy=disposable 환경에서 runbook bootstrap/disable/rollback 재현; failure=editorial 미검토·권리 미승인·로그인 미준비를 운영 완료로 표시하지 않음. Evidence task-22.txt.
  - Commit: 각 repo 별 `Document official corpus integration operations`. Depends: 21.

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit
  - 두 repo diff와 task-1~22 evidence를 읽기 전용 검토. 여섯 source 모두 raw→public projection 경로가 있고 2~5 요구가 빠짐없이 연결됨을 확인한다. 운영 input 미확보는 명시 BLOCKED로 남긴다. Evidence final-F1.txt.
- [ ] F2. Code quality review
  - 양쪽 공통 gate 재실행, migration/transaction/type/error 검토. implementation과 테스트가 함께 커밋됐는지 확인. command 실패나 required test skip이면 승인 금지. Evidence final-F2.txt.
- [ ] F3. Real manual QA
  - agent가 disposable API를 실제 호출하여 로그인 세션 fixture→catalog→analysis→roadmap→revoke 사용자 흐름을 검증한다. `JOBTOLOGY_INTEGRATION_REQUIRED=1 uv run pytest tests/integration/` 결과와 실제 응답 assertion 저장. 운영 DB 사용 금지. Evidence final-F3.txt.
- [ ] F4. Scope fidelity
  - 기존 local JSON/native v2 회귀, 인증 실패 차단, no raw/private data, source profile 정직성, 별도 repo commits, 기존 작업 보존을 검토한다. 운영 배포/로그인/새 데이터 소스가 몰래 추가되면 거절. Evidence final-F4.txt.

## Commit strategy
- **이번 요청:** BE의 이 계획 파일 하나만 신규 커밋·현재 upstream으로 일반 push. `.gitignore`, draft, 증거, 세션 로그는 stage하지 않는다. 코드/DB 저장소 변경은 없다. 기존 영문 imperative 스타일: `Plan official corpus and API integration`.
- **후속 구현:** DB/BE 각각 전용 branch/worktree. 작업별 구현+테스트를 atomic commit으로 묶고 DB producer 계약 변경을 BE consumer보다 먼저 배포한다. 공유 fixture 계약 SHA를 교차 확인한다. 각 repo의 당시 커밋 스타일을 다시 확인한다.
- force push, amend, hooks 우회, 무관한 사용자 변경 포함 금지. CI/검토 후 사용자 승인된 배포 절차만 따른다. main push는 기존 자동배포 workflow를 유발할 수 있으므로 문서 커밋도 그 사실을 보고한다.

## Success criteria
- Engineering DONE: 22 implementation tasks와 F1–F4 승인, required integration skip 0, 양 repo full gates 통과, 여섯 source deterministic publication/catalog/analysis/planning/revocation 흐름이 disposable 환경에서 입증됨.
- Production READY는 별도: 실제 six-source freshness/rights + 검토된 INTERNAL_EDITORIAL + migration/role/worker 준비 + 별도 로그인 과제 완료 + 운영 승인. 하나라도 없으면 기능 코드가 끝나도 운영 가능하다고 보고하지 않는다.
- 기존 v1 local JSON와 v2 native catalog 계약이 유지되고, 미준비/철회/오류를 빈 데이터나 임의 계산 결과로 숨기지 않는다.
- 이번 계획 문서의 커밋은 위 구현 또는 운영 검증을 수행했다는 뜻이 아니다.
