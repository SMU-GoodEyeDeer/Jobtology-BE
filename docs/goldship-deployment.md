# Goldship 배포 및 환경 가이드

> **문서 상태 (2026-09-25 조사 기준)**
>
> 이 문서는 Goldship 호스트의 읽기 전용 조사 결과와 Jobtology BE의 **권장 배포 절차**를
> 함께 기록한다. 실제 BE 배포, Coolify 설정 변경, 데이터베이스 쓰기, 자격 증명 열람은 이
> 조사나 문서 변경에서 수행하지 않았다. 따라서 권장 구성이 이미 존재한다고 해석해서는 안 된다.

## 표기와 범위

- **[관찰됨]**: 2026-09-25의 읽기 전용 조사에서 직접 확인한 사실이다.
- **[계획]**: 운영자가 이후에 만들거나 설정해야 하는 권장 구성이다.
- **[미확인]**: 조사하지 않았거나, 제한된 관찰만으로 결론 낼 수 없는 항목이다.
- 다이어그램의 `---->`는 관찰된 연결/구성을, `....>`는 계획 또는 확인 전 연결을 뜻한다.
- 다이어그램 안에는 가급적 ASCII 라벨만 사용하고, 한국어 설명은 다이어그램 밖에 둔다.

## 1. 현재 Goldship 관찰 결과

- **[관찰됨]** SSH는 `maxjo@goldship`로 성공했으며, 호스트는 Linux와 Docker를 사용한다.
- **[관찰됨]** Tailscale 주소는 `100.121.174.19`이다. 이는 관리 접근 관찰값이며, 공개 API
  주소나 DNS 레코드를 뜻하지 않는다.
- **[관찰됨]** Coolify `4.1.2`가 컨테이너를 관리한다.
- **[관찰됨]** Traefik `3.7`이 프록시로 동작하며 호스트 포트 `80`, `443`과 관찰된 `8080`을
  사용한다.
- **[관찰됨]** Coolify 관리 서비스는 호스트 `8000`에서 컨테이너 `8080`으로 연결된다.
- **[미확인]** Coolify 계정/권한은 SSH 계정과 별도로 확보해야 한다. SSH 접근 성공만으로
  Coolify 프로젝트, 애플리케이션, GitHub 연결 권한이 있는 것은 아니다.

```text
  Developer Mac
    |
    +----> SSH over Tailscale (100.121.174.19, verified)
             |
             v
  goldship (Linux / Docker)
    |
    +-- Coolify 4.1.2
    |     +-- management: host :8000 ----> container :8080
    |     `-- Jobtology-BE application: not identified
    +-- Traefik 3.7 (host :80, :443, observed :8080)
    |     `-- jobtology.yeongmin.net allPathPrefix('/') ----> jobtology-fe :3000
    +-- network: coolify
    |     +-- PostgreSQL 18: jobtology-fe / postgresql-database
    |     |     internal :5432, host j89dw8prq97uiec629avd6w5
    |     `-- jobtology-fe
    +-- network: graph-ui-1_svc-net-1
    |     +-- neo4j-1       host :7474 / :7687
    |     `-- neo4j-proxy-1 host :8443
    `-- coolify-db: platform DB; network unknown; never an application data target

  GitHub (private Jobtology-BE source)
    ....> FUTURE: Coolify fetch/build for a new BE application (not deployed)

  ----> observed configuration or route
  ....> planned or unverified connection
```

`jobtology-fe`는 실행 중인 프런트엔드 컨테이너다. Traefik은 `jobtology.yeongmin.net`의 모든 경로
접두사(`/`)를 FE로 보내므로, 현재 `/api`가 자동으로 BE로 전달된다고 추론하면 안 된다.

PostgreSQL 18 리소스는 `jobtology-fe` 프로젝트의 `postgresql-database`로 표기되고 `coolify`
네트워크에서 내부 `5432`와 위 호스트명을 가진다. 이는 FE 사용이나 스키마 소유권의 증거가 아니며,
인증 쿼리·스키마 조사는 하지 않았다.

`coolify-db`는 Coolify 플랫폼 DB이므로 **절대로 Jobtology 데이터 대상으로 쓰지 않는다.** 정확한
네트워크는 미확인이므로 다이어그램에도 임의의 연결을 그리지 않는다.

**[관찰됨]** 실행/중지 인벤토리에서 `backend-server` 또는 `JobtologyBE` 컨테이너를 찾지 못했다.
**[미확인]** 다른 이름의 과거 배포, 미배포 Coolify 앱 설정, BE 공개 도메인은 알 수 없다. 그러므로
현 인벤토리는 BE가 Goldship에 배포되어 있음을 증명하지 않는다.

## 2. 확인된 네트워크 프로브

다음은 `jobtology-fe`(Node `22.23.2`)의 연결성 확인일 뿐, 실제 설정·사용이나 권한 있는 데이터
접근을 증명하는 테스트가 아니다.

```text
  jobtology-fe (Node 22.23.2)
          |
          | DNS: neo4j-1.yeongmin.net
          v
  192.168.100.111
          |
          | TCP :443, TLS valid
          | HEAD /db/neo4j/query/v2 --> 401
          v
  Neo4j HTTPS endpoint

  jobtology-fe (Node 22.23.2)
          |
          | DNS: j89dw8prq97uiec629avd6w5
          v
  fd38:8774:b4fc::4
          |
          | TCP :5432 connected
          v
  PostgreSQL 18 resource

  ----> observed reachability only
```

- **[관찰됨]** `neo4j-1.yeongmin.net`은 `192.168.100.111`으로 해석됐고, TCP `443`/유효 TLS 및
  `HEAD /db/neo4j/query/v2`의 `401`을 확인했다.
- **[관찰됨]** PostgreSQL 호스트명은 `fd38:8774:b4fc::4`로 해석됐고 TCP `5432` 연결이 됐다.
- **[미확인]** 전체 HTTPS `443` 인입/프록시 체인, 인증 쿼리, DB 데이터·스키마 소유권, FE 실제 사용은
  검증하지 않았다.

## 3. 권장 BE 목표 구성

아래는 운영자가 Coolify에서 만들 구성이며, 도메인·공개 노출·DNS·TLS 발급은 미확인이라 점선이다.

```text
BUILD PATH (planned)
GitHub BE main ....> Coolify (existing) ....> Railpack build ....> [BE]

HTTP PATH (planned)
Browser ....> proposed API domain ....> Traefik :443 (existing) ....> [BE] :8000

DATA PATHS (planned)
[BE] ....> dedicated application PostgreSQL :5432 (planned)
[BE] ....> https://neo4j-1.yeongmin.net (observed endpoint)

[BE] = the same planned Jobtology-BE FastAPI container
API domain example = jobtology-api.yeongmin.net (not created)
Neo4j endpoint's full :443 ingress/proxy chain remains unknown
....> = planned or unverified connection
```

`jobtology-api.yeongmin.net`은 **권장 이름의 예시일 뿐 생성된 DNS나 서비스가 아니다**. 공개·Tailnet
전용 여부와 DNS/TLS는 운영자가 Coolify·네트워크 정책에서 확인한다.

기존 `jobtology.yeongmin.net`은 모든 경로를 FE에 보내므로 같은 호스트의 API에는 명시적 프록시/경로
규칙이 필요하다. FE API 환경변수명과 내부 `/api` 재작성은 미조사이므로 가정하지 않는다.

BE에는 전용 DB·사용자 또는 별도 애플리케이션 PostgreSQL을 권장한다. 기존 FE PostgreSQL은 소유권과
스키마가 인증 쿼리로 확인되기 전 재사용하지 않으며, `coolify-db`는 어떤 경우에도 대상이 아니다.

BE와 애플리케이션 PostgreSQL은 서로 도달 가능한 **올바른 동일 목적지 네트워크**에 연결한다. 플랫폼
DB 네트워크를 추측해 복제하지 말고 Coolify UI에서 확인하며, 컨테이너의 `localhost`는 BE 자신이다.

초기 catalog 전용 배포에는 worker가 필요 없다. 검토된 `local_json` 스냅샷 처리 때만 API와 분리된
one-shot worker를 같은 이미지·설정으로 운영한다.

```text
  OPTIONAL PLANNED local_json runtime

  API instance (planned) ....> application PostgreSQL (planned)
                              outbox / pinned contexts
                              ....> worker instance (planned, one-shot)
  reviewed local_json snapshot ....> worker instance (planned)
  worker instance (planned) ....> application PostgreSQL (planned, results)

  ....> optional planned flow
```

## 4. Coolify 배포 체크리스트

운영자는 아래를 Coolify UI에서 수행하며, 시작 전 Coolify 접근 계정·권한을 확보하고 SSH 계정과 같다고
가정하지 않는다.

1. **릴리스 대상 확인**
   - 배포할 정확한 커밋에서 [운영 런북의 사전 게이트](../deploy/README.md#pre-deploy-gate)를 통과시킨다.
   - private GitHub 저장소 `SMU-GoodEyeDeer/Jobtology-BE`의 `main`을 Coolify에 연결한다.
   - GitHub Secrets는 Coolify 런타임 변수로 자동 주입되지 않는다. 필요한 값은 Coolify에서 별도로
     등록한다.

2. **애플리케이션 만들기**
   - 새 Coolify 애플리케이션을 만들고 빌더로 Railpack을 선택한다.
   - 저장소 루트는 `.`로 지정한다. `.python-version`은 `3.12`이며 `railpack.json`은 다음 시작 명령을
     선언한다.

     ```sh
     uvicorn jobtology_be.main:app --host 0.0.0.0 --port ${PORT:-8000}
     ```

   - 서비스의 내부 노출 포트는 `8000`으로 설정한다. Coolify 관리 UI가 이미 호스트 `8000`을 쓰므로
     `host 8000:8000` 포트 바인딩을 만들거나 요구하지 않는다.
   - 오래된 Dockerfile 또는 시작 명령 override가 있으면 `railpack.json`과 충돌하지 않게 제거/비활성화한다.

3. **네트워크와 데이터베이스 연결**
   - 전용 애플리케이션 PostgreSQL 및 전용 DB/사용자를 준비하거나 별도 리소스를 만든다.
   - BE와 해당 DB 리소스를 서로 도달 가능한 동일 목적지 네트워크에 붙인다.
   - `JOBTOLOGY_DATABASE_URL`은 그 전용 DB를 가리키게 한다. FE DB 재사용은 소유권 확인 전 금지한다.
   - Neo4j는 아래 환경값으로 명시적으로 구성했을 때만 bounded native catalog 읽기에 사용한다.

4. **환경값 저장과 재배포**
   - DB 자격 증명은 Coolify에서 **Build time 비활성화, Runtime 활성화**로 저장한다.
   - 값을 저장한 뒤 애플리케이션을 restart 또는 redeploy한다. 저장만으로 실행 컨테이너 환경이 바뀌지
     않을 수 있다.
   - `.env`를 커밋·업로드하지 않으며, 자격 증명 값을 문서, 빌드 컨텍스트, 로그에 넣지 않는다. 이 조사에서
     기존 자격 증명은 읽지 않았다.

5. **첫 이미지와 마이그레이션 확인**
   - 초기에는 비공개로 배포하고 새 이미지의 터미널에서 migration 파일과 런타임 실행 파일이 포함됐는지
     먼저 확인한다.
   - 로컬 읽기 전용 확인에서 Alembic head는 `20260922_04`였다. 원격에서는 migration을 실행하지 않았다.
   - 릴리스 작업으로 한 번만 `alembic upgrade head`를 실행한다. 런타임에 `uv`가 있다고 보장하지 않으므로
     `uv run alembic upgrade head`는 `uv`가 실제로 있을 때에만 사용한다.
   - 첫 배포의 Pre-deployment Command에 의존하지 않는다. 문서화된 동작상 기존 컨테이너에서 실행되거나
     첫 배포에서는 건너뛸 수 있으며, 사용 중인 Railpack의 정확한 버전 동작은 확인하지 않았다.
   - API 복제본마다 독립적으로 migration을 실행하지 않는다. 이미지에 migration과 Alembic 실행 파일이
     포함돼 있는지 확인한 뒤 단일 릴리스 작업으로 수행한다.

6. **라우팅과 운영 검증**
   - 공개 API가 필요하면 별도 hostname 또는 명시적 경로 규칙을 만든다. `jobtology_api.yeongmin.net`은  
     예시일 뿐이며 생성·검증된 이름이 아니다.
   - 배포 후 [운영 런북의 수동 스모크 테스트와 롤백](../deploy/README.md#routing-and-manual-smoke-checks)을
     따른다. 이 가이드에 별도의 롤백 절차를 중복하지 않는다.

## 5. 런타임 환경값 예시

아래 예시에서 자격 증명과 새 BE PostgreSQL 값은 자리표시자다. Neo4j base URL과 FE origin은 관찰된
호스트명이지만, 자격 증명도 배포 완료 증명도 아니다. 앱 설정은 모두 `JOBTOLOGY_` 접두사를 사용한다
(`PORT`만 플랫폼 예외).

```dotenv
JOBTOLOGY_ENVIRONMENT=production
PORT=8000

JOBTOLOGY_DATABASE_URL=postgresql+asyncpg://<user>:<password>@<BE_DB_INTERNAL_HOST>:5432/<DB>

JOBTOLOGY_CORPUS_SOURCE=neo4j_query_api
JOBTOLOGY_DB_LINK=https://neo4j-1.yeongmin.net
JOBTOLOGY_DB_PASSWORD=<neo4j-query-api-password>

JOBTOLOGY_AUTH_ENABLED=false
JOBTOLOGY_ENABLE_FIXTURES=false
JOBTOLOGY_ENABLE_FE_MOCK_SAMPLES=false
JOBTOLOGY_CORS_ORIGINS=["https://jobtology.yeongmin.net"]
```

- `JOBTOLOGY_DB_LINK`는 `https://neo4j-1.yeongmin.net` **base URL만** 넣는다. `/db/neo4j/query/v2`
  경로나 query string을 넣지 않는다.
- 이 HTTPS `JOBTOLOGY_DB_LINK` 예시는 full URI이므로 `JOBTOLOGY_DB_PROTOCOL`을 생략한다.
  설정이 허용하는 `JOBTOLOGY_DB_PROTOCOL` 값은 `bolt://`, `bolt+ssc://`, `bolt+s://`, `neo4j://`,
  `neo4j+ssc://`, `neo4j+s://`뿐이다.
- PostgreSQL URL의 비밀번호에 `@`, `:`, `/`, `%` 등 URL 예약 문자가 있으면 percent-encode한다.
  `$`가 포함된 값은 Coolify의 Literal 값/variable expansion 옵션도 확인해 의도치 않은 치환을 막는다.
- `JOBTOLOGY_CORS_ORIGINS`는 JSON 배열이다. 자격 증명 CORS에 `*`를 쓰지 않는다. 위 FE origin은
  관찰된 프런트엔드 호스트를 위한 예시이며, 최종 공개 경계는 배포 후 정책과 함께 확인한다.
- `JOBTOLOGY_AUTH_ENABLED=false`에서는 보호된 product 요청이 세션 없이 `401 UNAUTHENTICATED`여야 한다.
  이 설정은 로그인이나 개발용 인증을 만들어 주지 않는다.
- `JOBTOLOGY_ENABLE_FIXTURES=false` 및 `JOBTOLOGY_ENABLE_FE_MOCK_SAMPLES=false`를 production에서 유지한다.
  fixture/mock을 켜도 사용자나 세션이 생성되지 않는다.

`JOBTOLOGY_CORPUS_SOURCE=neo4j_query_api`는 native catalog 읽기용이다. native 분석·계획 요청은 배포 성공과 무관하게
`503` 또는 `worker NATIVE_SOURCE_UNSUPPORTED`가 될 수 있으며, 이는 인프라 배포만으로 해결되지 않는다.

## 6. 배포 후 기대값과 한계

배포 후 공개 정책 범위에서 `/docs`, `/redoc`, `/openapi.json`, `/api-guide`를 확인할 수 있다.
`/api/v1/health/live`의 `200`은 프로세스 liveness일 뿐 DB·corpus·worker readiness 증명은 아니다.

인증 비활성 중 보호된 product endpoint의 `401`은 fail-closed 경계만 뜻하며 Google 로그인·인증 사용자
동작·원격 publish/revoke 통합을 증명하지 않는다. Google 인증은 후속 작업이다.

native catalog의 source contract와 검증 한계는 다음 문서를 함께 확인한다.

- [Native Neo4j source contract](neo4j-source-contract.md)
- [Historical Neo4j/local PostgreSQL verification record](neo4j-verification.md)
- [v2 architecture and product requirements](plan.md)

## 7. 공식 참고 자료

- [Coolify Railpack builds](https://coolify.io/docs/applications/builds/railpack)
- [Coolify environment variables](https://coolify.io/docs/applications/configuration/environment-variables)
- [Coolify general application configuration](https://coolify.io/docs/applications/configuration/general)

Coolify/Railpack의 UI·버전별 동작은 바뀔 수 있으므로 실제 설정 전 공식 문서와 현재 화면을 확인한다.
