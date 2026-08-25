# K8s Stateful Scale-Out — Findings (1주차 초안)

단일 노드 compose에서 잘 돌던 MCP Gateway를 3노드 k3d에 올리고 `replicas: 3`으로 밀었을 때
무엇이 깨지는지의 기록. **1주차 범위는 재현 절차 + 증상까지다.** 선택지 비교와 결론은 2주차 —
그중 세션 전략은 §1-A에서(T5), ticket-server는 §5에서(T6) 끝났다.

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

## 1-A. 두 세션 전략 — 실측 비교와 결론 ✅실측 (T5)

§1이 "`replicas: 3`이면 깨진다"까지였다면 여기는 **무엇을 택할 것인가**다. 후보는 둘이다.
A) Service `sessionAffinity: ClientIP`로 같은 클라이언트를 같은 파드에 고정한다.
B) `GATEWAY_MCP_STATELESS=1`로 세션 자체를 없앤다(T4의 토글).
둘 다 오버레이로 박제해 뒀다 — `k8s/overlays/session-affinity/`, `k8s/overlays/stateless/`.

| 측정 | A: affinity | B: stateless |
|---|---|---|
| Ingress 경유 e2e ×6 | **0 PASS / 6 FAIL** | **6 PASS / 0 FAIL** |
| Ingress 경유 LB 분포(30회) | 10 / 10 / 10 — **고정 안 됨** | 균등 |
| ClusterIP 직결 LB 분포(30회) | 30 / 0 / 0 — **완벽히 고정** | 흩어짐 |
| ClusterIP 직결 e2e ×6 | **6 PASS / 0 FAIL** | — |
| 고정 파드 kill 후 세션 | **끊김** | **생존** |

### A가 실패하는 첫 번째 이유 — 인그레스가 우회한다

같은 클러스터, 같은 Service, 같은 affinity 설정, 같은 순간이다. 유일한 차이는 요청이
kube-proxy를 지나느냐다. **affinity는 정상 동작한다 — 실제 트래픽이 그 경로로 안 갈 뿐이다.**

`sessionAffinity`는 kube-proxy가 **ClusterIP 트래픽에 대해** 구현한다. 그런데 k3s의 traefik은
ClusterIP를 쓰지 않는다. `--providers.kubernetesingress`가 nativeLB 없이 떠 있어서
EndpointSlice의 파드 IP를 읽어 **직접** LB한다.

```
Service ClusterIP : 10.43.245.45                            <- 경로에 없다
EndpointSlice     : 10.42.2.14 / 10.42.1.18 / 10.42.0.17    <- traefik이 직접 친다
```

외부 트래픽은 전부 traefik 파드 하나(`10.42.1.11`)에서 오므로 게이트웨이가 보는 클라이언트 IP는
**원래 1개다.** affinity가 경로에 있었다면 3파드가 아니라 1파드로 전부 몰렸어야 한다.
10/10/10이 나왔다는 건 kube-proxy가 애초에 관여하지 않았다는 뜻이다.

**가장 나쁜 점은 조용하다는 것이다.** `kubectl get svc gateway`는 `sessionAffinity: ClientIP`를
정상 출력한다(타임아웃 10800초까지). 설정은 적용됐고 이벤트도 경고도 로그도 없다. 아무 일도 안
할 뿐이다. "yaml에 썼으니 됐겠지"가 통하지 않는 종류의 실패다.

traefik 어노테이션(`service.nativelb`)으로 경로에 넣을 수는 있다. 그런데 고쳐도 두 번째 이유가 남는다.

### A가 실패하는 두 번째 이유 — 파드 재시작을 못 넘는다

affinity가 실제로 동작하는 경로(ClusterIP 직결)에서 세션을 열고, 고정된 파드를 죽이고,
같은 세션으로 다시 호출했다.

```
BEFORE_KILL isError=False
AFTER_KILL  McpError: Session terminated
            Session termination failed: 404
```

같은 실험을 B에서 하면 `AFTER_KILL isError=False` — 그냥 지나간다.

고정은 "어느 파드로 갈지"를 정할 뿐 **세션 상태를 복제하지 않는다.** 그 파드가 사라지면 상태도
사라진다. K8s에서 파드 재시작은 사고가 아니라 일상이다 — rollout, eviction, node drain,
scale-down. **배포할 때마다 진행 중인 모든 세션이 끊긴다.**

### 결론 — B(stateless)를 택한다

A는 독립된 두 이유로 기각된다. 인그레스 우회는 고칠 수 있지만 파드 재시작은 못 고친다(세션 상태
복제는 이 프로젝트가 만들려는 것보다 크다). 여기에 3주차 KEDA와의 자기모순이 겹친다 — 고정된
클라이언트는 스케일아웃을 나눠 받지 못한다.

**B의 대가는 정직하게 적어 둔다.** stateless는 audit 쪼개짐(§2)도 rate limit 배증(§3)도
해결하지 않는다. 다만 그 둘은 stateless가 **만든** 문제가 아니다 — affinity에서도 버킷과 파일은
똑같이 파드 로컬이다. stateless는 숨을 곳을 없앴을 뿐이다.

### 1C 재정리 — 결정이 하나 줄었다

원래 프레이밍은 "affinity를 고르면 rate limit이 부수적으로 해결되지만 3주차 KEDA가 자기모순이
된다, stateless는 그 반대"였다. **그 트레이드오프는 존재하지 않는다.** affinity가 선택지가 아니므로
rate limit은 세션 전략과 무관하게 **독립적으로** 풀어야 하는 문제다. 2주차가 결론낼 결정은 둘이
아니라 하나고(세션 전략 = B 확정), 나머지는 개별 문제로 내려간다.

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

## 5. ticket-server — `replicas: 3`에서 조용한 소실 ✅실측 (T6)

**가설은 절반만 맞았다.** "빈 결과로 조용히 사라진다"는 실재하지만 **소수 경로**다.
지배적 증상은 조용한 소실이 아니라 **시끄러운 `BACKEND_UNAVAILABLE`** — §1의 MCP 세션 문제가
한 홉 안쪽(게이트웨이→백엔드)에서 똑같이 재현된다. 저장소가 갈라지는 걸 보기 전에 세션 계층이
먼저 부러진다.

**측정.** ticket-server만 `replicas: 3`으로 올리고, 매 시도마다 게이트웨이에 새로 붙어서 측정.

| 시나리오 | HIT | **SILENT_MISS** | LOUD(`BACKEND_UNAVAILABLE`) |
|---|---|---|---|
| 기준선 `replicas: 1`, create+search 왕복 ×3 | 3 | 0 | 0 |
| ticket 3 / 게이트웨이 1, 왕복 ×15 | 2 | 0 | **13** |
| ticket 3 / 게이트웨이 1, search-only ×12 | 4 | **2** | 6 |
| ticket 3 / 게이트웨이 3 + stateless, 왕복 ×9 | 0 | 0 | **9** |

`search-only`는 이미 만들어진 티켓(`t6-e1b-9`)을 세션마다 새로 붙어 찾는다. 7·8번째 시도가
`hits=0` **+ isError 없음** — 가설이 말한 바로 그 장면이다. 12번 중 2번.

**왜 대부분 시끄러운가.** 게이트웨이는 백엔드당 MCP 세션 1개를 유지한다(`upstream.py`). 그 세션의
POST가 Service ClusterIP를 거쳐 라운드로빈되므로 세션을 발급한 파드가 아닌 곳에 닿으면 404다.
재연결도 같은 이유로 실패한다 — `initialize`는 아무 파드에서나 200이지만 바로 다음 POST가 다른
파드로 가면 그 파드는 방금 발급된 세션을 모른다. 파드 로그가 그대로 보여준다.

```
# 셋 다 같은 게이트웨이 파드(10.42.2.15)에서 나간 요청이다
ticket-server-...-ln25z:  POST /mcp 200 OK          ← 세션을 발급한 파드
ticket-server-...-cknqk:  POST /mcp 404 Not Found
ticket-server-...-vctbt:  POST /mcp 404 Not Found
```

**저장소가 실제로 갈라진다는 증거.** 스키마는 매 연결마다 `CREATE TABLE IF NOT EXISTS`로 자가
부트스트랩된다(`db.py`의 `_connect`). 그래서 "테이블 없음"은 **그 파드에서 tool이 한 번도 실행된
적 없다**는 뜻이고, "테이블 있음 + 0행"은 **읽기가 거기서 돌았고 아무것도 못 찾았다**는 뜻이다.
search-only 직후 파드별 스냅샷:

| 파드 | 상태 | 해석 |
|---|---|---|
| ln25z | `table=yes rows=26` | 성공한 쓰기가 전부 여기로 갔다 |
| cknqk | `table=yes rows=0` | search가 여기서 돌았다 → **SILENT_MISS 2건의 정체** |
| vctbt | `table=no` | tool이 한 번도 실행되지 않았다 |

> 주의: 파일 존재 여부(`os.path.exists`)로는 판정할 수 없다. 확인하려고 `sqlite3.connect`를 하는
> 순간 빈 파일이 생겨 측정이 오염된다(이번에 한 번 겪었다). 판정 기준은 **테이블 유무**다.

**`/ready`는 거짓말하지 않는다.** 실패 중 `{"ticket":false,...}`를 정확히 보고했다(T3의 능동 probe).
§1-A의 `sessionAffinity`가 `kubectl`에 멀쩡히 보이면서 아무 일도 안 하던 것과 정반대다. 여기서
조용한 건 **데이터**지 헬스 신호가 아니다.

**stateless 토글은 이 홉을 덮지 않는다.** `GATEWAY_MCP_STATELESS`는 게이트웨이의 *서버* 쪽
(클라이언트→게이트웨이)만 stateless로 만든다(`app.py:203-204`). 게이트웨이가 백엔드를 향해 여는
*클라이언트* 세션(`upstream.py`)은 그대로 stateful이다. 그래서 §1-A가 택한 전략 B로 게이트웨이를
3개로 늘리면 백엔드 세션도 3개가 되어 실패가 오히려 **9/9로 악화**된다.

**결론 — `replicas: 1` 고정.** 근거 셋:
① 지배적 실패가 시끄러워서(13/15, 9/9) 스케일아웃이 애초에 성립하지 않는다.
② 성립하는 소수 경로에서도 파드 로컬 SQLite라 쓰기가 흩어지고, 그 조회는 에러 없이 빈 결과다.
③ 전략 B(stateless)가 이 홉을 덮지 않으므로 §1-A의 결론으로 해결되지 않는다.
진짜 해결은 공유 저장소(외부 DB 또는 PVC)이고 그건 설계상 범위 밖이다(`db.py` 모듈 docstring —
외부 의존성 없는 통제 환경). **`replicas: 1`은 타협이 아니라 이 백엔드의 정확한 현재 상태다.**

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
