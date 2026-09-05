---
status: handoff
branch: main
timestamp: 2026-09-05T17:00:00+09:00
files_modified:
  - TODOS.md (T11·T12·T13 신설 + T7 범위 명확화 + T9 구조 재정의 + 착수 순서 재정렬)
  - docs/design/k8s-stateful-scale-out.md (§2026-09-05 계획 공백 보완 추가)
  - docs/context/2026-09-05-context-save.md (본 문서)
  - certs/windows-roots.crt (git 미추적 — 머신 로컬 재생성분)
---

## Working on: 계획 공백 4건 보완 + K8s 환경 재구축 — T7 착수 직전에서 중단

### Summary

08-25(T6) 이후 11일 공백을 두고 재개했다. 두 가지를 했다.

1. **계획의 빈 곳을 메웠다.** 남은 구간을 점검하니 "어떻게 측정할지"는 다 있는데
   **"무엇으로 결론낼지"가 세 항목에서 비어 있었다.** T11·T12·T13을 신설하고 T9의 구조를
   다시 정의했다. 커밋 `efb1428`, 푸시 완료.
2. **클러스터를 처음부터 다시 세웠다.** T1 때와 **다른 머신**이라 k3d도 CA 인증서도 없었다.
   기준선까지 복구해 Ingress 경유 e2e **exit 0**을 다시 확인했다.

코드는 한 줄도 안 건드렸다. T7(`scripts/verify_scaleout.py`)은 설계 조사만 하고 미착수.

---

## 1. 계획 공백 4건 — 무엇이 비어 있었나

설계 2주차 표는 **세션 / audit / rate limit / circuit breaker** 네 항목을 각각
"재현 → 증거 → 선택지 비교 → 결론"으로 끝내라고 요구한다. 점검 결과:

| 항목 | 실측 | 결론 | 조치 |
|---|---|---|---|
| MCP 세션 | ✅ T5 | ✅ stateless 확정 | — |
| ticket-server | ✅ T6 | ✅ `replicas: 1` 고정 | — |
| **audit (§2)** | 부분 | ❌ **태스크 자체가 없었음** | **T11 신설** |
| **circuit breaker (§4)** | ❌ | ❌ **실측·결론 둘 다 없었음** | **T12 신설** |
| rate limit (§3) | ❌ | 소유자 없음 | T7=측정 / **T9 §3=결정**으로 분리 |
| README 진입점 (성공기준 #11) | — | 태스크 없음 | **T13 신설** |

**T9의 "두 결정" 구조는 폐기했다.** T5가 그 전제(affinity를 고르면 rate limit이 부수
해결된다)를 실측으로 부정했으므로, 새 구조는 **결정 1개(세션 전략 = stateless) + 독립
문제 4개**다. 상세는 `TODOS.md` § A, 요약은 설계 문서 § "2026-09-05 계획 공백 보완".

작업 정본은 `TODOS.md`다. 설계 문서에는 공백 목록과 Open Questions 상태만 적고 되풀이하지
않았다 — D6에서 "문서 세 곳이 서로 달랐다"를 겪었기 때문.

**새 착수 순서: T7 → T11 → T12 → T9 → T13 → T8/T10.**

---

## 2. 환경 재구축 — 이 절이 다음 세션에서 제일 중요하다

**T1(08-21)과 다른 머신이다.** 레포는 OneDrive로 동기화됐지만 도구와 머신 로컬 파일은
따라오지 않았다. 아래 5단계를 그대로 다시 밟으면 기준선까지 간다.

### ① k3d 재설치 (미설치 상태였음)

```powershell
winget install --id k3d.k3d --silent --accept-package-agreements --accept-source-agreements
```

설치 후 PATH가 갱신되므로 **새 셸**이 필요하다. 이 세션에서는 매 PowerShell 호출마다
아래를 앞에 붙여 우회했다.

```powershell
$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
```

k3d v5.9.0 / k3s v1.35.5+k3s1 — findings의 재현 환경과 동일. kubectl v1.34.1은 Docker
Desktop 번들로 이미 있었다.

### ② `certs/windows-roots.crt` 재생성 (`.gitignore` 대상이라 부재)

findings §8의 CA 주입이 이 파일을 요구하는데 `certs/*.crt`가 gitignore라 레포에 없다.
Windows 루트 저장소에서 PEM으로 다시 뽑는다(45개 인증서, 73KB).

```powershell
$path = "certs\windows-roots.crt"
$sb = New-Object System.Text.StringBuilder
foreach ($c in (Get-ChildItem Cert:\LocalMachine\Root)) {
  [void]$sb.AppendLine("# " + $c.Subject)
  [void]$sb.AppendLine("-----BEGIN CERTIFICATE-----")
  [void]$sb.AppendLine([Convert]::ToBase64String($c.RawData,'InsertLineBreaks'))
  [void]$sb.AppendLine("-----END CERTIFICATE-----")
}
Set-Content -Path $path -Value $sb.ToString() -Encoding ascii
```

### ③ 클러스터 생성 + CA **선제** 주입

```bash
k3d cluster create agentops --agents 2 \
  --registry-create agentops-registry:0.0.0.0:5111 -p "8080:80@loadbalancer"

for n in k3d-agentops-server-0 k3d-agentops-agent-0 k3d-agentops-agent-1; do
  docker cp certs/windows-roots.crt "$n:/etc/ssl/certs/windows-roots.crt"
  docker exec "$n" sh -c 'cat /etc/ssl/certs/windows-roots.crt >> /etc/ssl/certs/ca-certificates.crt'
done
docker restart k3d-agentops-agent-0 k3d-agentops-agent-1 k3d-agentops-server-0
```

**선제 주입이 먹혔다 — traefik `ImagePullBackOff` 재발 없음.** findings §8은 사후 대응
절차였는데, 생성 직후 바로 심으면 증상 자체가 안 난다. 다음에도 이 순서로.

### ④ 【신규 함정】 `host.docker.internal`이 VPN 어댑터 IP로 풀린다

노드 재시작 뒤 `kubectl`이 전부 타임아웃했다.

```
Get "https://host.docker.internal:55164/api": dial tcp 10.207.111.24:55164: ... 응답 없음
```

`host.docker.internal`이 **10.207.111.24**(VPN/가상 어댑터)로 해석된다. API 포트는
`0.0.0.0:55164`에 정상 게시돼 있었으므로 kubeconfig만 루프백으로 돌리면 끝난다.

```bash
kubectl config set-cluster k3d-agentops --server=https://127.0.0.1:55164
```

포트는 클러스터를 다시 만들 때마다 바뀐다 — `docker port k3d-agentops-serverlb`로 확인.
k3s 서버 로그는 멀쩡했고 노드도 살아 있었다. **클러스터가 아니라 호스트 이름 해석 문제다** —
서버 로그부터 보고 컨테이너를 재시작하는 데 시간을 쓰지 말 것.

### ⑤ 이미지 4종 빌드·푸시 → 배포

```bash
docker build -q -f gateway/Dockerfile        -t localhost:5111/agentops/gateway:dev .
docker build -q -f servers/ticket/Dockerfile -t localhost:5111/agentops/ticket-server:dev .
docker build -q -f servers/docs/Dockerfile   -t localhost:5111/agentops/docs-server:dev .
docker build -q -f servers/ops/Dockerfile    -t localhost:5111/agentops/ops-server:dev .
for n in gateway ticket-server docs-server ops-server; do docker push -q localhost:5111/agentops/$n:dev; done

kubectl apply -k k8s/base
kubectl wait --for=condition=available --timeout=240s deployment --all
```

호스트는 `localhost:5111`로 push하고 매니페스트는 `agentops-registry:5000`을 참조한다(D7).

### 기준선 검증 — 통과

```bash
GATEWAY_URL=http://localhost:8080/mcp GATEWAY_JWT_SECRET=demo-secret-do-not-use-in-prod \
  uv run --project gateway python scripts/e2e_demo.py
# [e2e] scenario complete: success-success-denied / EXIT=0
```

---

## 3. 현재 상태

- **git:** `main`, working tree clean, `efb1428` 푸시 완료. origin과 동기.
- **테스트:** `tests/unit` 75 passed (이번 세션 실측). 전체 109(unit 75 + integration 34)는
  코드 미변경이라 유지로 본다.
- **클러스터:** 살아 있다. 6서비스 전부 `1/1 Running`, 3노드 분산, 전 Deployment
  `replicas: 1`(기준선), stateless 토글 미설정.
  - 세션을 새로 시작하면 `kubectl get nodes`부터 확인하고, 실패하면 위 ④를 적용할 것.
  - 클러스터를 지우려면 `k3d cluster delete agentops`.

---

## 4. 다음 작업 — T7부터

### T7 착수 메모 (이번 세션 조사분, 코드 미작성)

`scripts/verify_scaleout.py` — 한 줄 실행으로 세 증거를 뽑는다(결정 6A).
`spike_concurrency.py`가 패턴 참고용이다.

**rate limit 측정 설계 — `GATEWAY_RATE_REFILL=0`이 핵심.**
`ratelimit.py:43`의 refill 기본값이 `capacity`(초당 통이 가득 참)라 그대로 두면 실효 한도를
셀 수 없다. `GATEWAY_RATE_REFILL=0`을 주면 토큰이 회복되지 않아 **"거부 전까지 몇 번
통과했나"가 곧 실효 한도**가 된다.

```
GATEWAY_RATE_LIMIT=5, GATEWAY_RATE_REFILL=0
  replicas 1 → 5회 통과 후 RATE_LIMITED 예상
  replicas 3 → 15회 (= 5 × 3) 예상 ← 이 배수가 §3의 증거
```

- 파싱: `capacity = int(cap)` (미승인 제안 1번 — 값 검증 없음). 정수만 줄 것.
- 거부 형태: HTTP 429가 아니라 **MCP `CallToolResult(isError=True)` + `{"code":"RATE_LIMITED"}`**
  (`errors.py`의 `error_result`). `content[0].text`를 JSON 파싱해 판정한다.
- 버킷 키는 IP가 아니라 **agent**(`allow(self, agent)`)다. 클라이언트를 나눠도 소용없고,
  파드가 나뉘어야 배증한다.
- `route_call` 0단계라 tool 해석·정책보다 **먼저** 걸린다 — 어떤 tool로 재도 무방.
- replicas 3에서 요청이 실제로 흩어지려면 `GATEWAY_MCP_STATELESS=1`이 필요하다(T5 결론).
  안 그러면 rate limit이 아니라 세션이 먼저 깨진다.

**ticket 소실 / 세션 전략 차이**는 T6·T5의 측정을 스크립트로 옮기는 것이다. 절차와 판정
기준(HIT / SILENT_MISS / LOUD, "테이블 유무"로 파드 상태 판정)은
`docs/k8s-stateful-findings.md` §5에 그대로 있다.

**끝나면 클러스터를 기준선으로 되돌릴 것** — T5·T6도 그렇게 했다
(`replicas: 1`, 토글 미설정, 매니페스트 파일은 안 건드림).

### 그다음

- **T11** audit 결론 (§2, 성공기준 #4) — local-path가 RWO만 준다는 제약이 곧 서사
- **T12** circuit breaker 실측 + 기각 근거 (§4) — `GATEWAY_CIRCUIT_THRESHOLD` 미설정이면
  회로가 통째로 비활성이라 재현이 성립하지 않는다. **먼저 확인할 것.**
- **T9** findings 완성 (결정 1 + 독립 문제 4 구조)
- **T13** README 진입점 / **T8** architecture.md / **T10** 커버리지

---

## 5. 미결·주의

- **D5 재결정이 여전히 막혀 있다.** 3~4주차 착수 여부의 판단 근거가 "JD 5건"인데 수집은
  **0건**이다. C 트랙 최우선 항목이고, 한 시간이면 2주짜리 결정을 검증한다.
- **Evidence Box(A 트랙)는 PARKED 그대로.** 잠금 해제 조건(2차 대화 일정)에 변동 없음.
- **성공기준 #10의 "97 테스트"는 현재 109다.** 취지(회귀 없음)는 유효해서 숫자만 설계
  문서에 기록하고 본문은 두었다.
- **gstack 업그레이드 가능:** 1.64.0.0 → 1.79.0.0. 이번 세션에서는 안 했다.
