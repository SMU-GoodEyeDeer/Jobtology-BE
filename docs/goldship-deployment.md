# Goldship 배포 및 환경 가이드

> **문서 상태 (2026-09-25 배포 기준)**
>
> Jobtology BE는 Goldship의 Coolify에 배포되어 `https://jobtology.yeongmin.net/api`로 공개되어 있다.
> Google 로그인은 아직 구현되지 않았으므로 모든 product API는 `401 UNAUTHENTICATED`를 반환한다.
> 이 문서에는 자격 증명 값이 없으며, 값은 Coolify 환경 변수와 GitHub Secret에만 있다.

## 1. 공개 주소

FE와 BE는 같은 origin을 쓴다. Traefik이 `/api`로 시작하는 요청을 BE로 보내고, 나머지는 FE로 보낸다.

| 용도 | 주소 |
| --- | --- |
| 제품 API | `https://jobtology.yeongmin.net/api/v1/...` |
| Neo4j 카탈로그 API | `https://jobtology.yeongmin.net/api/v2/...` |
| Swagger UI | `https://jobtology.yeongmin.net/api/docs` |
| ReDoc | `https://jobtology.yeongmin.net/api/redoc` |
| OpenAPI JSON | `https://jobtology.yeongmin.net/api/openapi.json` |
| 한국어 FE 연동 가이드 | `https://jobtology.yeongmin.net/api/guide` |
| Liveness | `https://jobtology.yeongmin.net/api/v1/health/live` |
| FE 컨테이너 → BE (Coolify 내부 네트워크) | `http://jobtology-be:8000` |

FE는 같은 origin의 상대 경로(`/api/...`)로 호출하면 되며 별도 API 도메인이나 CORS가 필요 없다.

```text
  browser
     |
     | https://jobtology.yeongmin.net
     v
  Cloudflare (DNS proxy + tunnel)
     |
     v
  goldship: Traefik 3.7 (coolify-proxy)
     |                                   |
     | Host && PathPrefix(/api)          | Host && PathPrefix(/)
     v                                   v
  jobtology-be :8000                  jobtology-fe :3000
     |            \
     |             \ HTTPS Query API (read-only catalog)
     v              v
  jobtology-be-postgres :5432      neo4j-1.yeongmin.net:443
  (network: coolify)
```

라우터 규칙은 길이가 긴 쪽이 우선하므로 `/api` 규칙이 FE의 `/` 규칙보다 먼저 매칭된다. BE 앱은 Coolify의
**strip prefix를 끈 상태**여야 한다. BE 자신이 `/api` 아래 경로를 제공하기 때문이다.

## 2. Coolify 리소스

모든 리소스는 Coolify 프로젝트 `Jobtology-BE`의 `production` 환경에 있고, 서버 `localhost`(goldship)의
`coolify` 네트워크에 연결되어 있다.

| 리소스 | 이름 / UUID | 설정 |
| --- | --- | --- |
| 애플리케이션 | `jobtology-be` / `w131qqclc6wyke8347y9puqo` | GitHub App `jobtology`, `SMU-GoodEyeDeer/Jobtology-BE@main`, Railpack, 노출 포트 `8000`(호스트 바인딩 없음), 도메인 `https://jobtology.yeongmin.net/api`, 네트워크 별칭 `jobtology-be`, push 자동배포 끔 |
| 헬스체크 | — | `GET /api/v1/health/live` on `8000`, start period 30s |
| PostgreSQL 18 | `jobtology-be-postgres` / `fwxdtqu77iy8ghcjk52v5woe` | DB `jobtology`, 사용자 `jobtology`, 비공개(`is_public=false`) |
| DB 백업 | `azbun92damca0n1x79kwnw22` | 매일 `0 18 * * *` UTC(03:00 KST), 로컬 14개 보관, S3 없음. 경로 `/data/coolify/backups/databases/root-team-0/jobtology-be-postgres-fwxdtqu77iy8ghcjk52v5woe/` |

기존 FE용 PostgreSQL(`jobtology-fe` 프로젝트)과 `coolify-db`는 BE가 사용하지 않는다.

## 3. 런타임 환경 변수

모두 **Runtime 전용**(Build time 비활성)이고 **Literal**(변수 치환 없음)로 저장되어 있다. 값을 바꾼 뒤에는
redeploy해야 실행 중인 컨테이너에 반영된다.

| 변수 | 값 |
| --- | --- |
| `JOBTOLOGY_ENVIRONMENT` | `production` |
| `PORT` | `8000` |
| `JOBTOLOGY_DATABASE_URL` | `postgresql+asyncpg://jobtology:<secret>@fwxdtqu77iy8ghcjk52v5woe:5432/jobtology` |
| `JOBTOLOGY_CORPUS_SOURCE` | `neo4j_query_api` |
| `JOBTOLOGY_DB_LINK` | `https://neo4j-1.yeongmin.net` (base URL만) |
| `JOBTOLOGY_DB_PASSWORD` | `<secret>` — Neo4j `neo4j` 계정 |
| `JOBTOLOGY_AUTH_ENABLED` | `false` |
| `JOBTOLOGY_ENABLE_FIXTURES` | `false` |
| `JOBTOLOGY_ENABLE_FE_MOCK_SAMPLES` | `false` |
| `JOBTOLOGY_CORS_ORIGINS` | `["https://jobtology.yeongmin.net"]` |

Neo4j는 Coolify가 관리하지 않는다. `maxjo@goldship:/home/maxjo/jobtology/docker-compose.yaml`로 따로 운영되는
**Community 에디션**이라 읽기 전용 역할을 만들 수 없다. 그래서 BE는 관리자 계정 `neo4j`를 쓰며, 코드 수준에서
bounded 읽기 쿼리만 실행한다. Neo4j 비밀번호를 바꾸면 이 변수도 함께 갱신해야 한다.

## 4. 배포 흐름

`main`에 push하면 [CI](../.github/workflows/ci.yml)가 실행된다.

1. `checks`: `uv sync --locked --dev`, `ruff check`, `pytest`, `uv lock --check`, `uv build`.
2. `deploy`(`main` push에서만, `checks` 성공 후): `main`의 HEAD가 여전히 이 커밋일 때만
   `https://coolify.yeongmin.net/api/v1/deploy?uuid=w131qqclc6wyke8347y9puqo`를 호출하고, 배포가
   `finished`가 될 때까지 기다린다. 더 새 커밋이 올라왔다면 그 커밋의 실행에 배포를 맡기고 건너뛴다.

GitHub Secret `COOLIFY_TOKEN`은 `read` + `deploy` 권한만 가진 Coolify 토큰(`github-actions-jobtology-be-deploy`)이다.
Coolify의 push 자동배포는 꺼져 있으므로, CI가 실패한 커밋은 배포되지 않는다.

수동 배포가 필요하면 Coolify UI에서 **Redeploy**를 누르거나 같은 deploy API를 호출한다.

## 5. 마이그레이션

Alembic은 이미지 안의 `/app/.venv/bin/alembic`에 있다(이미지에는 `uv`, `curl`, `wget`도 있다). 현재 적용된
revision은 `20260922_04 (head)`이다.

새 migration이 들어간 커밋을 배포할 때는 새 컨테이너에서 **한 번만** 실행한다.

```sh
ssh maxjo@goldship
CN=$(docker ps --filter name=w131qqclc6wyke8347y9puqo --format '{{.Names}}' | head -1)
docker exec -w /app "$CN" alembic upgrade head
docker exec -w /app "$CN" alembic current
```

migration은 이전 이미지와 호환되게(backward-compatible) 작성한다. 그래야 이미지 롤백이 안전하다.
운영 DB에 `alembic downgrade`를 함부로 실행하지 않는다. Coolify Pre-deployment Command는 쓰지 않는다.

## 6. 롤백

Coolify는 이 앱의 이미지를 최근 2개 보관한다(`docker_images_to_keep=2`). UI의 **Rollback** 탭에서 이전
커밋 이미지를 고르면 재빌드 없이 약 45초 만에 전환된다. 2026-09-25에 `d3c4729` → `2c6e8c8` → `d3c4729`
롤백·복귀를 리허설했고, 두 번 모두 liveness `200`을 확인했다.

롤백 뒤에는 아래 스모크 체크를 다시 수행한다.

## 7. 스모크 체크

| 요청 | 기대값 |
| --- | --- |
| `GET /api/v1/health/live` | `200` |
| `GET /api/docs`, `/api/redoc`, `/api/openapi.json`, `/api/guide` | `200` |
| 인증 없는 product 요청 (예: `GET /api/v1/me/profile`, `GET /api/v2/occupations`) | `401` + `UNAUTHENTICATED` envelope |
| `GET /` | FE 페이지 `200` (BE가 FE 라우팅을 가로채지 않음) |

`/api/v1/health/live`는 프로세스 생존만 증명한다. DB·Neo4j 준비 상태는 보장하지 않는다. `401`은 fail-closed
경계만 증명하며 Google 로그인이나 인증된 사용자 흐름을 증명하지 않는다.

## 8. 남은 작업

- **Google 로그인**: login/callback HTTP 라우트가 아직 없다. 구현한 뒤 Google OAuth 클라이언트의 redirect URI를
  `https://jobtology.yeongmin.net/api/v1/auth/google/callback` 형태로 등록하고, `JOBTOLOGY_GOOGLE_*`,
  `JOBTOLOGY_FRONTEND_URL=https://jobtology.yeongmin.net`, `JOBTOLOGY_AUTH_ENABLED=true`를 설정한다.
- **local_json 분석·worker**: `neo4j_query_api` 설정에서는 분석 서비스가 비활성이다. 분석 흐름을 쓰려면
  스냅샷 볼륨과 one-shot worker(Coolify Scheduled Task, `python -m jobtology_be.workers.main`)가 필요하다.
- **오프사이트 백업**: 현재 백업은 goldship 로컬 디스크에만 있다. S3 저장소 연결과 복구 리허설이 필요하다.

## 9. 관련 문서

- [운영 런북](../deploy/README.md)
- [Native Neo4j source contract](neo4j-source-contract.md)
- [v2 architecture and product requirements](plan.md)
- [Coolify Railpack builds](https://coolify.io/docs/applications/builds/railpack)
- [Coolify environment variables](https://coolify.io/docs/applications/configuration/environment-variables)
