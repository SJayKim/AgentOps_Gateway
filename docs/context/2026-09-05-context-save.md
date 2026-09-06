---
status: current
branch: main
timestamp: 2026-09-06T00:00:00+09:00
covers: 2026-09-05 (계획 공백 + 환경 재구축) → 2026-09-06 (2주차 P1 종료)
files_modified:
  - scripts/verify_scaleout.py (신규 — T7)
  - k8s/overlays/audit-pvc/ (신규 3파일 — T11, 기각된 안의 박제)
  - docs/k8s-stateful-findings.md (§2·§3·§4 결론 + §9 + 요약표 + 조용한 실패 4종 — T9)
  - docs/design/k8s-stateful-scale-out.md (§2026-09-05 계획 공백 보완, §2026-09-06 2주차 P1 종료)
  - TODOS.md (T7·T11·T12·T9 완료 반영)
  - docs/context/2026-09-05-context-save.md (본 문서 — current status로 갱신)
  - certs/windows-roots.crt (git 미추적 — 머신 로컬 재생성분)
---

## Current Status: 2주차 P1 종료 (T7·T11·T12·T9 완료) — 남은 것은 P2 3건

### Summary

두 세션을 이어 붙인 문서다. **§1·§2는 09-05 세션의 기록**(환경 재구축 절차는 머신이 바뀌면
다시 필요하므로 그대로 보존), **§3부터가 현재 상태**다.

**09-05 (계획):** 남은 구간을 점검하니 "어떻게 측정할지"는 다 있는데 **"무엇으로 결론낼지"가
세 항목에서 비어 있었다.** T11·T12·T13을 신설하고 T9 구조를 재정의(`efb1428`). 클러스터는
다른 머신이라 처음부터 다시 세웠다(`cda4074`).

**09-06 (실행):** **2주차 P1 4건을 전부 끝냈다.** T7(증거 수집 스크립트) → T11(audit 결론) →
T12(circuit breaker 결론) → T9(findings 완성). 커밋 4건. **네 개의 독립 문제가 전부 "고치지
않는다"로 끝났고 근거가 넷 다 다르다** — 그게 이 트랙의 산출물이다.

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

## 3. 2026-09-06 진행분 — 2주차 P1 4건 (T7 → T11 → T12 → T9)

### T7 — `scripts/verify_scaleout.py` (`786ef7a`)

한 명령으로 증거 3종. 스크립트가 replicas·env를 직접 조작하고 **finally에서 기준선으로
되돌린다**(매니페스트는 안 건드림).

| 측정 | 결과 |
|---|---|
| §3 rate limit 실효 한도 | `replicas 1` → **5**, `replicas 3 + stateless` → **15 (×3.0)** |
| §5 ticket 소실 | `HIT=1 / SILENT_MISS=2 / LOUD=9`, 파드 저장소 `rows=2/0/0` |
| §1-A 세션 전략 | stateful **0/6**, stateless **6/6** (T5 재현) |

### T11 — audit 결론 (`d58d548`)

**PVC(RWO)는 완결성을 실제로 준다.** 같은 부하(18건)에서 파드 로컬 `6/6/6` → PVC `18/18/18`,
`/admin` 6건 → 18건, 세 파드 동시 append에도 **찢긴 줄 0**.

**대가가 스케일아웃 자체다.** PVC를 붙이면 세 파드가 전부 한 노드에 뜬다. 강제다 — 그 노드를
cordon하고 파드를 지우면 `0/3 nodes are available: ... 2 node(s) didn't match PersistentVolume's
node affinity`로 Pending. 설계 ⑤(`sharedFileSystemPath`)도 닫혔다: 세 노드가
`/var/lib/rancher/k3s`에 **각자 다른 Docker 볼륨**을 갖는다.
→ **안 고침.** 택한 방향은 audit을 stdout으로 내보내 표준 로그 수집에 태우기(3주차).

### T12 — circuit breaker 결론 (`22c5ed2`)

선행 확인에서 먼저 걸렸다 — 매니페스트에 `GATEWAY_CIRCUIT_THRESHOLD`가 없어 **회로가 켜져
있지도 않았다.** 켜고 한 파드에만 실패를 먹이니 `없음/있음/있음`으로 갈렸고, 같은 클라이언트가
인그레스로 12번 물으면 **ops 보임 8 / 안 보임 4**. 복구는 그 파드만 스스로 했다.
→ **안 고침.** 회로가 지키는 것은 백엔드가 아니라 **그 파드의 연결**이다.

### T9 — findings 완성 (`c053359`)

계획 대비 둘이 커졌다: **"안 고침"이 2종 → 4종**(audit·rate limit도), **조용한 실패가 3종 →
4종**(§9 추가). §번호는 재번호하지 않고 **맨 위 요약표**로 "결정 1 + 독립 문제 4"를 전달한다 —
세 문서가 §번호를 인용하고 있어 D6의 문서 불일치를 다시 만들지 않기 위해서.

### 이번 세션의 새 함정 — findings §9

**`kubectl rollout status`는 "옛 파드가 요청을 그만 받는 시점"이 아니다.** ReplicaSet이
`deletionTimestamp` 찍힌 파드를 active에서 빼므로 삭제 표시 순간 완료로 보고되고, 그 파드는
graceful termination 동안 계속 서빙한다. **한 세션에 두 번 걸렸다** — T7에서는 `replicas: 1`인데
`Session terminated`, T12에서는 `replicas: 0`으로 줄인 백엔드가 `OK`를 반환. `replicas: 0`은
기다릴 새 파드가 없어 특히 조용하다. 해결은 `verify_scaleout.py`의 `settle()`.

---

## 4. 현재 상태

- **git:** `main`, working tree clean. 09-06 커밋 4건(`786ef7a` `d58d548` `22c5ed2` `c053359`).
- **검증:** Ingress 경유 e2e **exit 0**, **109 passed**(unit 75 + integration 34), ruff 통과,
  kustomize 4종 빌드(base + stateless + session-affinity + audit-pvc).
- **클러스터:** 살아 있다. 6서비스 전부 `1/1 Running`, 3노드 분산. **기준선 복귀 확인 완료** —
  전 Deployment `replicas: 1`, 토글 3종 없음, PVC 없음, cordon 흔적 없음.
  - 세션을 새로 시작하면 `kubectl get nodes`부터 확인하고, 실패하면 위 ②④를 적용할 것.
  - 클러스터를 지우려면 `k3d cluster delete agentops`.
- **2주차 P1은 전부 끝났다.** 남은 것은 P2 3건.

---

## 5. 다음 작업 — P2 3건 + 상시

- **T13 (P2, ~10분)** README 최상단 "K8s에서 배운 것" 3줄 + findings 링크 (성공기준 #11).
  **이게 남은 것 중 값어치가 제일 크다** — findings를 아무리 잘 써도 진입점이 없으면 안 읽힌다.
- **T8 (P2, ~5분)** `docs/architecture.md` — 201행 `/ready` 누락, 8장 폴더 지도에 `k8s/` 없음.
- **T10 (P2, ~20분)** 커버리지 갭 12건 중 코드 경로 7건.
- **C 트랙 JD 5건 수집 — 여전히 0건.** D5(3~4주차 착수 여부)가 여기 묶여 있다. 한 시간이면
  2주짜리 결정을 검증한다. **P2보다 이게 먼저일 수 있다.**

---

## 6. 미결·주의

- **CLAUDE.md Gotcha 2줄 제안 — 미승인.** ① `rollout status` 함정(위 §3, 한 세션에 두 번 걸림)
  ② 스크립트 출력에 `sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)` 고정
  (Windows cp949가 `—` 하나에 죽고, 파이프는 블록 버퍼라 진행이 안 보인다). CLAUDE.md가
  "제안 후 승인 대기"를 요구해 쓰지 않았다.
- **§2·§3의 "안 고침"은 증거 기반 판단이고 뒤집을 수 있다.** audit은 "stdout → 로그 수집",
  rate limit은 "`N`은 파드당 한도라는 계약 명시"로 적었다. 근거는 findings에 전부 있다.
- **성공기준 #7은 선행 조건이 있다.** `GATEWAY_CIRCUIT_THRESHOLD`가 매니페스트에 없어 회로가
  비활성이다. 기본 비활성은 stretch의 의도된 설계라 `k8s/base/`는 그대로 뒀다 — 켤지는 3주차 결정.
- **D5 재결정이 여전히 막혀 있다.** 판단 근거가 "JD 5건"인데 수집은 **0건**.
- **Evidence Box(A 트랙)는 PARKED 그대로.** 잠금 해제 조건(2차 대화 일정)에 변동 없음.
- **성공기준 #10의 "97 테스트"는 현재 109다.** 취지(회귀 없음)는 유효해서 숫자만 설계
  문서에 기록하고 본문은 두었다.
- **gstack 업그레이드 가능:** 1.64.0.0 → 1.79.0.0. 두 세션 다 안 했다.
