# K8s Stateful Scale-Out — Findings (1주차 초안)

단일 노드 compose에서 잘 돌던 MCP Gateway를 3노드 k3d에 올리고 `replicas: 3`으로 밀었을 때
무엇이 깨지는지의 기록. **1주차 범위는 재현 절차 + 증상까지다.** 선택지 비교와 결론은 2주차.

설계: `docs/design/k8s-stateful-scale-out.md`

## 재현 환경

| 항목 | 값 |
|---|---|
| 클러스터 | k3d v5.9.0 / k3s v1.35.5+k3s1, server 1 + agents 2 (**3노드**) |
| 생성 | `k3d cluster create agentops --agents 2 --registry-create agentops-registry:0.0.0.0:5111 -p "8080:80@loadbalancer"` |
| 이미지 반입 | k3d 내장 레지스트리 (D7). 호스트에서 `localhost:5111`로 push, 매니페스트는 `agentops-registry:5000` 참조 |
| 매니페스트 | `k8s/base/` — Deployment×6, Service×6, Ingress×1(gateway + grafana), ConfigMap×3, Secret×1 |
| 배포 | `kubectl apply -k k8s/base` |
| 검증 | `GATEWAY_URL=http://localhost:8080/mcp GATEWAY_JWT_SECRET=... uv run --project gateway python scripts/e2e_demo.py` |

기준선(`replicas: 1`)에서 Ingress 경유 e2e는 **exit 0**, 성공-성공-거부 시나리오 통과.
`/health` `/ready` `/metrics` `/grafana` 전부 200. `/ready`는 백엔드 3종 모두 `true`.

## 1. MCP 세션 — `replicas: 3`에서 전면 파손 ✅실측

**증상.** 게이트웨이만 `replicas: 3`으로 올리고 e2e를 6회 돌리면 **6회 전부 실패**한다.

```
run 1..6: FAIL   mcp.shared.exceptions.McpError: Session terminated
=== replicas=3 결과: PASS=0 FAIL=6 ===
```

간헐이 아니라 **전면**이다. `StreamableHTTPSessionManager`가 `initialize` 응답으로 발급한
`mcp-session-id`를 발급한 파드만 알고 있는데, 다음 POST가 라운드로빈으로 다른 파드에 가면
그 파드에는 세션이 없다. 요청마다 POST인 Streamable HTTP에서는 첫 왕복 이후 거의 모든 요청이
어긋난다. 확률이 아니라 구조다.

**대조군 — `GATEWAY_MCP_STATELESS=1`이면 6/6 통과.**

```
kubectl set env deployment/gateway GATEWAY_MCP_STATELESS=1
=== replicas=3 + STATELESS=1 결과: PASS=6 FAIL=0 ===
```

토글은 T4에서 넣었다(`app.py:203-204`). **이 대조군이 2주차 선택지 비교의 전제 조건이다** —
stateless 경로가 실제로 도는 것이 확인됐으므로 `sessionAffinity`와 실측으로 비교할 수 있다.
무엇을 잃는지(세션 상태에 기대는 MCP 기능)의 판단은 2주차.

## 2. audit 로그 — 파드마다 쪼개진다 ✅실측

**증상.** stateless로 6회 e2e(도구 호출 18건)를 돌린 뒤 파드별 `audit.jsonl` 줄 수:

```
gateway-...-drnsr : 6 줄
gateway-...-kttcm : 7 줄
gateway-...-vrqml : 5 줄
```

한 줄도 유실되지 않았지만 **한 곳에도 모이지 않는다.** `/admin`은 HTTP 200을 주면서 자기
파드가 가진 조각만 보여준다 — 화면은 멀쩡하고 내용만 틀린, 가장 나쁜 실패 모양이다.

**2주차로 넘기는 이유이자 제약:** k3d의 local-path 프로비저너는 **RWO만** 준다. RWX가 없는
환경에서 무엇을 택했는지가 곧 서사다(PVC / StatefulSet+파드별 파일 / 사이드카 수집).

## 3. rate limit — 실효 한도가 레플리카 수만큼 늘어난다 ⏳2주차 실측

`ratelimit.py:39`의 버킷은 파드 로컬 `dict`이고 키가 IP가 아니라 **agent**다(`allow(self, agent)`).
파드가 3개면 같은 agent가 3개의 독립 버킷을 갖는다 → 정책이 조용히 3배로 위반된다.

재현 절차: `GATEWAY_RATE_LIMIT`을 작은 값으로 두고 `replicas: 3`에서 한도 초과까지 연속 호출,
429가 나오는 지점을 센다. 기준선(`replicas: 1`) 대비 몇 배인지가 증거.

**여기가 1C와 얽힌다.** `sessionAffinity: ClientIP`를 고르면 이 문제가 부수적으로 사라지지만,
3주차 KEDA가 자기모순이 된다(고정된 클라이언트는 스케일아웃을 나눠 받지 못한다). stateless는
그 반대다. **문제는 4개가 아니라 2개의 결정이다.**

## 4. circuit breaker — 파드마다 다르게 학습한다 ⏳2주차 실측

`circuit.py`의 `_failures`/`_opened_at`이 파드 로컬이라 같은 백엔드에 대해 파드마다 판단이
갈리고, `tools/list` 결과가 파드마다 달라진다.

**네 문제 중 유일하게 "안 고침"이 답일 가능성이 높다.** 파드가 자기 연결의 건강을 스스로
판단하는 것이 옳다는 논증을 세우는 쪽이 공유 상태를 넣는 쪽보다 낫다. 2주차에 기각 근거로 쓴다.

## 5. ticket-server — `replicas: 3`에서 조용한 소실 ⏳2주차 실측 (T6)

SQLite 파일이 파드 로컬이라 생성이 간 파드와 검색이 간 파드가 다르면 방금 만든 티켓이 안 보인다.
에러가 아니라 **빈 결과**로 나오는 것이 핵심 — 실패가 실패처럼 안 생겼다.
1주차 매니페스트는 `replicas: 1`로 고정해 뒀다.

## 6. Prometheus — `replicas: 3`에서 카운터가 튄다 📌기록만, 안 고침

`observability/prometheus.yml`이 `static_configs: targets: ["gateway:8000"]`이다. K8s에서 이
이름은 Service ClusterIP로 풀리므로 매 스크레이프가 **임의의 파드**를 잡는다. 파드마다 자기
카운터만 갖고 있으니 시계열이 세 값을 오간다.

**고치지 않는다.** ServiceMonitor는 3주차(D5 보류)이고, 이 증상 자체가 "왜 ServiceMonitor가
필요한가"의 실증이다. 지금 고치면 그 논거가 사라진다.

## 7. MCP 세션 핸들이 백엔드보다 오래 산다 ✅실측 (T3)

**K8s에서 "파드는 Ready인데 요청은 전부 실패"의 교과서 사례.**

Streamable HTTP는 요청마다 POST라 유휴 중에 백엔드가 죽어도 연결 소유 task가 `stop.wait()`에서
깨지 않고, `upstream.py:64-65`의 `finally: self._session = None`이 돌지 않는다. 실측: 백엔드를
kill하고 1초 뒤에도 `ensure_session()`이 **0.000초에 "성공"**하며 죽은 세션을 그대로 돌려준다.

즉 `ensure_session()`만 부르는 readiness probe는 **죽은 백엔드에 Ready를 준다.** MCP는 요청을
실제로 보내봐야 죽음을 안다. `/ready`가 `send_ping()`까지 왕복하는 이유다(`app.py:271`).

**매니페스트에 건 제약:** 그 ping은 즉시 실패하지 않고 2초 타임아웃까지 매달린다. 그래서
`readinessProbe.timeoutSeconds`가 **3 이상**이어야 한다. 그 미만이면 kubelet이 먼저 끊어
멀쩡한 파드가 NotReady가 된다.

## 8. TLS 인터셉터 — k3d 노드가 docker.io를 못 당긴다 ✅실측 (T1, 설계가 예측함)

**증상.** 클러스터 생성 직후 traefik이 `ImagePullBackOff`.

```
failed to pull image "rancher/mirrored-library-traefik:3.6.13":
  tls: failed to verify certificate: x509: certificate signed by unknown authority
```

호스트 Docker는 Windows 루트 저장소를 갖고 있어 우리 이미지 4종의 빌드·push는 전부 성공했다.
그러나 **k3d 노드는 별도 컨테이너의 containerd**라 인터셉터 CA가 없다. 우리 이미지는 로컬
레지스트리(평문 HTTP)라 무사했고, 밖에서 당겨야 하는 컴포넌트만 걸렸다. prom/grafana는
통과해서 **간헐적으로 보이지만**, 원인은 확정적이다.

`k3d image import`로 우회하려다 실패했다 — `ctr: content digest ...: not found`.

**해결.** 노드에 CA를 심고 재시작한다. containerd(Go)는 cert pool을 프로세스 시작 시 1회
캐시하므로 재시작이 필수다.

```bash
for n in k3d-agentops-server-0 k3d-agentops-agent-0 k3d-agentops-agent-1; do
  docker cp certs/windows-roots.crt "$n:/etc/ssl/certs/windows-roots.crt"
  docker exec "$n" sh -c 'cat /etc/ssl/certs/windows-roots.crt >> /etc/ssl/certs/ca-certificates.crt'
done
docker restart k3d-agentops-agent-0 k3d-agentops-agent-1 k3d-agentops-server-0
```

설계 1주차가 "TLS 인터셉터 이슈가 여기서 재현될 수 있음"이라고 적어둔 그대로다.

## 부수 기록 — kustomize가 루트 밖 파일을 거부한다

`configMapGenerator`의 `files:`에 `../../policies/policy.yaml`을 쓰면 실패한다.

```
security; file '...\policies\policy.yaml' is not in or below '...\k8s\base'
```

그래서 ConfigMap 소스를 `k8s/base/config/`에 복사해 뒀다. 사본은 조용히 썩으므로
`tests/unit/test_k8s_config_drift.py`가 원본 5종과의 일치를 고정한다(사본을 한 줄만 바꿔도
실패하는 것을 확인).
