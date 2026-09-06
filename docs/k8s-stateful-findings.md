# K8s Stateful Scale-Out — Findings

단일 노드 compose에서 잘 돌던 MCP Gateway를 3노드 k3d에 올리고 `replicas: 3`으로 밀었다.
무엇이 깨졌고, 무엇을 고쳤고, **무엇을 고치지 않기로 했는지**의 기록.

**한 줄 요약: 다섯 군데가 깨졌는데 트레이드오프가 있는 결정은 하나뿐이었다.** 나머지 넷은
"이 환경에서는 이렇게 동작한다"를 알아내는 문제였고, **넷 다 고치지 않기로 끝났다 — 서로 다른
네 가지 이유로.** 아무것도 안 했다는 뜻이 아니다. 네 개의 "안 고침"에는 각각 다른 근거가 있고,
그 근거를 만드는 것이 2주차 작업의 대부분이었다.

증거는 한 명령으로 재생된다.

```bash
GATEWAY_JWT_SECRET=<secret> GATEWAY_URL=http://localhost:8080/mcp \
  uv run --project gateway python scripts/verify_scaleout.py
```

설계: `docs/design/k8s-stateful-scale-out.md`

## 요약 — 결정 1개, 독립 문제 4개

| | 무엇이 깨지나 | 결론 | 결론의 근거 한 줄 |
|---|---|---|---|
| **결정** — 세션 전략 (§1, §1-A) | `replicas: 3`에서 e2e **0/6** | **stateless 채택**, affinity 기각 | affinity는 인그레스가 우회하고, 고쳐도 파드 재시작을 못 넘는다 |
| 문제 1 — audit (§2) | `/admin`이 자기 조각만 (18건 중 6건) | **안 고침** + 해결 방향 명시 | PVC는 완결성을 주지만 세 파드를 한 노드에 못박는다 |
| 문제 2 — rate limit (§3) | 실효 한도가 **정확히 ×3** | **안 고침** + 계약으로 명시 | 공유 카운터는 외부 의존성, 나눗셈은 3주차 KEDA와 자기모순 |
| 문제 3 — circuit breaker (§4) | 파드마다 `tools/list`가 갈린다 | **안 고침** — 파드별이 의미적으로 옳다 | 회로가 지키는 것은 백엔드가 아니라 **그 파드의 연결**이다 |
| 문제 4 — ticket-server (§5) | 조용한 소실 + 시끄러운 실패 | **`replicas: 1` 고정** | 저장소가 갈라지기 전에 세션 계층이 먼저 부러진다 |

원래 프레이밍은 **"두 개의 결정"** 이었다 — affinity를 고르면 rate limit이 부수적으로 해결되지만
3주차 KEDA가 자기모순이 되고, stateless는 그 반대라는 것. **그 트레이드오프는 존재하지 않았다**
(§1-A). affinity가 선택지가 아니므로 나머지 넷은 세션 전략과 무관한 **독립 문제**로 내려간다.
결정은 하나, 나머지는 각자의 근거로 각자 끝난다.

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

토글은 T4에서 넣었다(`app.py:203-204`). **이 대조군이 선택지 비교의 전제 조건이었다** —
stateless 경로가 실제로 도는 것이 확인됐으므로 `sessionAffinity`와 실측으로 비교할 수 있다.
무엇을 잃는지의 판단은 §1-A에서 끝난다.

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

**그리고 이것이 B의 가장 중요한 한계다 — 전략 B는 게이트웨이→백엔드 홉을 해결하지 않는다.**
`GATEWAY_MCP_STATELESS`는 게이트웨이의 *서버* 쪽(클라이언트→게이트웨이)만 stateless로 만든다
(`app.py:203-204`). 게이트웨이가 백엔드를 향해 여는 *클라이언트* 세션(`upstream.py`)은 그대로
stateful이다. 그래서 백엔드를 여러 개로 늘리면 §1과 **똑같은 파손이 한 홉 안쪽에서 재현되고**,
게이트웨이를 3개로 늘리면 백엔드 세션도 3개가 되어 실패가 오히려 **9/9로 악화된다**(§5 실측).
B는 "클라이언트가 어느 게이트웨이 파드에 닿아도 된다"만 산다. 백엔드 쪽 세션 문제는 그대로다.

### 1C 재정리 — 결정이 하나 줄었다

원래 프레이밍은 "affinity를 고르면 rate limit이 부수적으로 해결되지만 3주차 KEDA가 자기모순이
된다, stateless는 그 반대"였다. **그 트레이드오프는 존재하지 않는다.** affinity가 선택지가 아니므로
rate limit은 세션 전략과 무관하게 **독립적으로** 풀어야 하는 문제다. 2주차가 결론낸 결정은 둘이
아니라 하나고(세션 전략 = B 확정), 나머지는 개별 문제로 내려갔다 — 문서 맨 위의 요약표가 그 결과다.

## 2. audit 로그 — 파드마다 쪼개진다 ✅실측 + 결론 (T11)

**증상.** stateless로 6회 e2e(도구 호출 18건)를 돌린 뒤 파드별 `audit.jsonl` 줄 수:

```
gateway-...-drnsr : 6 줄
gateway-...-kttcm : 7 줄
gateway-...-vrqml : 5 줄
```

한 줄도 유실되지 않았지만 **한 곳에도 모이지 않는다.** `/admin`은 HTTP 200을 주면서 자기
파드가 가진 조각만 보여준다 — 화면은 멀쩡하고 내용만 틀린, 가장 나쁜 실패 모양이다.
재측정에서도 같다: 3파드 `6/6/6`, `/admin` 상세 행 **6건**(전체 18건 중).

깨지는 것은 **무결성이 아니라 완결성**이다. `audit.py`는 append-only JSONL이고 수정·삭제
경로가 없다 — 어느 조각도 위조되지 않았다. 없는 것은 "전부를 한 번에 보는 시야"다.

**재현.**

```bash
kubectl apply -k k8s/overlays/stateless        # 또는 k8s/overlays/audit-pvc
# 파드가 '준비된 새 파드 정확히 3개'로 정착한 뒤(§9) e2e를 6회 — 호출 18건
for p in $(kubectl get pods -l app=gateway -o name); do
  kubectl exec "$p" -- sh -c 'wc -l < /app/audit/audit.jsonl'
done
curl -s "http://localhost:8080/admin?token=<ADMIN_TOKEN>" | grep -c '</tr>'
```

### 선택지 ① PVC(RWO) — 완결성은 실제로 산다. 대가가 스케일아웃이다

오버레이로 박제해 뒀다 — `k8s/overlays/audit-pvc/`. 같은 부하(e2e 6회 = 호출 18건):

| | 파드 배치 | 파드별 audit | `/admin` |
|---|---|---|---|
| 현행 (파드 로컬) | 3노드에 분산 | 6 / 6 / 6 | 6건 (자기 조각) |
| PVC(RWO) | **전부 한 노드** | **18 / 18 / 18** | **18건 (전부)** |

PVC는 정말로 고친다. 세 파드가 한 파일에 동시에 append했는데 **전 줄이 유효 JSON, 찢긴 줄 0**
이다(작은 `O_APPEND` 쓰기는 원자적이다). 그런데 파드 배치 칸을 보라 — 셋이 **같은 노드**에 떴다.

우연이 아니라 강제다. local-path가 만든 PV에 `nodeAffinity`가 박히고, 그 볼륨을 마운트하는
파드는 그 노드로만 갈 수 있다. 그 노드를 `cordon`하고 파드를 하나 지우면:

```
0/3 nodes are available: 1 node(s) were unschedulable,
2 node(s) didn't match PersistentVolume's node affinity.
```

새 파드는 다른 노드로 **못 간다**. 성능이 떨어지는 게 아니라 스케줄이 안 된다. 노드 장애에
견디려고 레플리카를 셋으로 늘리는데, 그 셋을 한 노드에 묶어 애초의 목적을 없앤다.

**덤으로 잠자던 위험을 깨운다.** `admin.py:49`는 매 요청 파일 전체를 `read_text()`한다. 지금은
파일이 파드와 함께 사라져서 문제가 안 드러났을 뿐이다. PVC를 붙이면 파일이 무한히 자라고
로테이션이 없다.

### 선택지 ⑤ local-path `sharedFileSystemPath` — 탈출구가 아니다

설계는 "k3d는 노드가 전부 같은 Docker 호스트의 컨테이너라 모든 노드에 같은 경로를 마운트하는
조건을 만들 수 있다"고 봤다. **실측은 반대다.** 세 노드는 `/var/lib/rancher/k3s`에 각자 다른
Docker 볼륨을 갖는다.

```
k3d-agentops-agent-0 : volume e2df089807…/_data -> /var/lib/rancher/k3s
k3d-agentops-agent-1 : volume a331e1850f…/_data -> /var/lib/rancher/k3s
k3d-agentops-server-0: volume deddc08079…/_data -> /var/lib/rancher/k3s
```

공유되는 것은 이미지 볼륨(`k3d-agentops-images`)뿐이다. agent-1의 저장 경로에 파일을 만들면
agent-0에는 **디렉터리조차 없다.** 이 옵션을 켜면 RWX라는 **선언**만 얻는다 — PV의 노드 고정이
풀려 파드는 흩어지고, 저장소 계층이 "공유"라고 말하는 채로 audit은 **조용히 다시 쪼개진다.**
설계가 경고한 "k3d가 단일 호스트라서 준 거짓 통과"보다 나쁜 모양이다.

### 선택지 ② StatefulSet + `/admin` 팬아웃 — 완결성이 아니라 시늉

파드별 파일을 유지하고 `/admin`이 전 파드를 조회해 합치는 안. 기각한다. `/admin`의 데이터
소스를 바꾸는 코드 변경이고(미승인 제안 2번과 같은 자리), 무엇보다 **죽은 파드의 조각은 영영
못 읽는다.** 조각을 모으는 시늉이지 완결성이 아니다.

### 결론 — 고치지 않는다. 대신 사실을 명시하고 방향을 정한다

**게이트웨이 `replicas: 3`에서 `/admin`은 자기 파드의 조각만 보여준다.** 이것이 현재 상태이고,
숨기지 않고 적는 것이 2주차의 결론이다(성공기준 #4의 "못 보여주는 이유 + 택한 대안").

**택한 대안: audit을 파일이 아니라 stdout(JSONL)으로 내보내 K8s 표준 로그 수집 경로에 태운다.**
공유 볼륨도 팬아웃도 필요 없고, 파드가 죽어도 이미 나간 줄은 수집기에 남는다. 근본 원인은
K8s가 아니라 **audit이 파드 로컬 파일이라는 설계**이고, 이 방향만이 그 원인을 건드린다.
코드 변경이므로 **3주차 관측 스택 구간(D5 보류)** 에 둔다.

## 3. rate limit — 실효 한도가 레플리카 수만큼 늘어난다 ✅실측 + 결론 (T7)

`ratelimit.py`의 버킷은 파드 로컬 `dict`이고 키가 IP가 아니라 **agent**다(`allow(self, agent)`).
파드가 3개면 같은 agent가 3개의 독립 버킷을 갖는다.

**측정.** `GATEWAY_RATE_LIMIT=5`, `GATEWAY_RATE_REFILL=0`으로 토큰 회복을 끊으면 통과한 횟수가
곧 실효 한도다(refill 기본값은 capacity라 그대로 두면 시간에 의존해 셀 수 없다).

| 배치 | 30회 호출 중 통과 | 실효 한도 |
|---|---|---|
| `replicas: 1` | 5 | **5** (설정값 그대로) |
| `replicas: 3` + stateless | 15 | **15 (×3.0)** |

가설이 그대로, 오차 없이 나왔다. `RATE_LIMITED`는 HTTP 429가 아니라 MCP `isError` 결과의
구조화 payload로 온다(`errors.py`) — 게이트웨이의 계약이 HTTP가 아니라 MCP이기 때문이다.

### 선택지 셋 다 기각한다

**① 공유 카운터(Redis 등).** 정확하지만 설계 제약("외부 의존성 없는 완전 통제 환경")과 정면
충돌하고, 모든 tool 호출에 네트워크 왕복을 하나 더 얹는다 — rate limit은 `route_call`의 0단계,
즉 **모든 호출이 반드시 지나는 자리**다.

**② 한도를 레플리카 수로 나눈다.** 산술은 맞아 보이는데 세 군데서 무너진다.
(a) **3주차 KEDA가 레플리카 수를 동적으로 만든다** — 나눗셈의 분모가 스케일아웃마다 바뀌므로
설정값이 클러스터 상태를 따라다녀야 한다. 자기 자신을 참조하는 고리다.
(b) 균등 분배를 가정한다. 위 측정이 정확히 ×3이 된 건 라운드로빈 + **클라이언트 하나**였기
때문이고, 클라이언트가 여럿이면 성립하지 않는다.
(c) `ratelimit.py:42`가 `capacity = int(cap)`이다. 10을 3으로 나눈 값을 ConfigMap에 쓰면
`int("3.33")`이 ValueError로 **기동 실패**한다(미승인 제안 1번).

**③ 게이트웨이 앞단(traefik 미들웨어)으로 옮긴다.** 버킷 키가 **agent**(JWT claim)인데 traefik의
`rateLimit` 미들웨어는 IP 기준이다. 우리 정책의 단위와 맞지 않는다 — 옮기려면 인그레스가 우리
토큰을 파싱해야 하고, 그건 게이트웨이의 일을 인그레스로 옮기는 것이다.

### 결론 — 고치지 않는다. 대신 계약으로 적는다

**`GATEWAY_RATE_LIMIT=N`은 파드당 한도다. 실효 한도는 `N × 레플리카 수`다.** 이것이 이 코드의
정확한 의미이고, 성공기준 #3이 허용한 "왜 그렇게 하지 않기로 했는지의 근거"에 해당한다.
설정값이 뜻하는 바를 바꾸지 않고 **그 뜻을 정확히 적는 것**이 지금 할 수 있는 정직한 일이다.
공유 카운터가 필요해지는 시점은 rate limit이 데모 장치가 아니라 과금·SLA 경계가 될 때다.

## 4. circuit breaker — 파드마다 다르게 학습한다 ✅실측 + 결론 (T12)

**먼저 알아야 할 것 — 클러스터에서 회로는 켜져 있지도 않았다.** `circuit.py`의 `from_env()`는
`GATEWAY_CIRCUIT_THRESHOLD` 미설정 시 `None`을 돌려 회로 차단을 통째로 비활성화한다(기본 비활성
opt-in). `k8s/base/`에도 `docker-compose.yml`에도 이 값이 없다. **재현하려면 먼저 켜야 한다** —
`kubectl set env deployment/gateway GATEWAY_CIRCUIT_THRESHOLD=2 GATEWAY_CIRCUIT_COOLDOWN=15`.

**재현.** 인그레스로는 어느 파드가 답했는지 알 수 없으므로 파드에 직접 붙는다. `tools/list`는
`is_tripped`만 읽고 `allow`/`record`를 부르지 않으므로 **관측이 상태를 바꾸지 않는다.**

```bash
kubectl set env deployment/gateway \
  GATEWAY_CIRCUIT_THRESHOLD=2 GATEWAY_CIRCUIT_COOLDOWN=15 GATEWAY_MCP_STATELESS=1
kubectl scale deployment/gateway --replicas=3
kubectl scale deployment/ops-server --replicas=0   # 파드가 실제로 사라질 때까지 대기 (§9)

kubectl port-forward pod/<gateway-pod-A> 18099:8000   # 이 파드에만 실패를 먹인다
#   dev-agent 토큰으로 ops__get_metrics {"metric":"cpu"} ×3  → BACKEND_UNAVAILABLE ×3
# 그 뒤 파드마다 port-forward해 tools/list의 ops 노출을 비교한다
```

`dev-agent`여야 한다 — `support-agent`는 정책(4단계)에서 먼저 걸려 회로(5단계)까지 못 간다.

**측정.** 회로를 켜고 `replicas: 3` + stateless. `ops-server`를 죽이고, **파드 하나에만**
실패를 먹인다. 나머지 둘은 아무 일도 겪지 않는다.

| 단계 | 파드별 `tools/list`에 `ops`가 있나 |
|---|---|
| 기준선 | `2ljsw: 있음 / mqz9t: 있음 / qxff2: 있음` |
| `ops-server` 사망 직후 | `있음 / 있음 / 있음` ← **아직 아무도 모른다** |
| 한 파드에 실패 3회(threshold 2) | **`없음`** `/ 있음 / 있음` ← 갈렸다 |
| `ops-server` 복구 + cooldown 후 probe 1회 | `있음 / 있음 / 있음` ← 그 파드만 스스로 복구 |

실패 3회는 전부 `BACKEND_UNAVAILABLE`이었고, 그 파드의 회로만 open이 됐다.

**에이전트가 겪는 모습.** 같은 클라이언트가 같은 순간에 인그레스로 `tools/list`를 12번 물으면:

```
ops 보임 = 8 / 안 보임 = 4      (파드 3개 중 1개만 회로가 내려간 상태 = 정확히 2/3)
```

같은 질문에 다른 답이 온다. 도구 카탈로그가 **누가 받았느냐에 따라 달라진다.**

### 그런데 이게 옳다 — 회로가 지키는 것은 백엔드가 아니라 *그 파드의 연결*이다

`upstream.Backend._session`은 파드마다 별개다. 파드 A의 세션이 죽었다는 사실은 파드 B의 세션에
대해 **아무것도 말해주지 않는다.** 이건 추정이 아니라 §5에서 실측한 그림이다 — 같은 순간 같은
백엔드에 대해 어떤 게이트웨이 파드는 성공하고 어떤 파드는 404를 받았다. §7은 한 발 더 나가서,
MCP 세션 핸들이 백엔드보다 오래 살기 때문에 **죽음은 연결마다 따로 드러난다**고 말한다.

**공유 상태를 넣으면 양방향으로 틀린다.**
- 파드 A의 깨진 연결이 멀쩡한 B·C의 호출까지 차단한다 → 멀쩡한 용량을 버린다.
- 반대로 B·C의 성공이 A의 죽은 연결을 계속 "정상"으로 유지한다 → A는 매 요청을 죽은 연결에
  매달고, 회로 차단기의 존재 이유인 fail-fast가 사라진다.

**수렴은 저절로 된다.** 위 divergence는 실패 정보를 **한 파드에만** 준 인위적 조건에서 나왔다.
백엔드가 진짜로 죽고 트래픽이 계속 돌면 세 파드가 각자 배워 전부 open이 된다. 정보가 균등하게
오면 상태도 균등해진다 — 갈림은 **아직 모르는 파드가 있다**는 뜻이지 틀린 상태가 아니다.

**회복도 파드별이 옳다.** cooldown 후 half-open probe 1회가 **자기 연결로** 검증한다. 다른 파드가
성공했다는 이유로 내 죽은 연결을 열어 주면, 그 파드의 다음 요청은 확실히 실패한다.

### 결론 — 고치지 않는다. 다만 대가를 적어 둔다

**대가는 `tools/list`가 파드마다 다를 수 있다는 것이다**(위 8/4). 이를 없애려면 회로가 열려도
목록에서는 빼지 않고 호출 시점에만 fail-fast하면 된다. 그러면 카탈로그는 안정되지만 "죽은 tool을
에이전트에게 보이지 않게 한다"는 `aggregate.py`의 원래 목적을 버리게 된다. 두 성질을 동시에 가질
수는 없고, **이 데모에서는 후자가 더 중요하다** — 에이전트가 스스로 계획을 세우는 것이 전제이므로
계획 단계에서 죽은 도구를 지우는 편이 낫다.

> 성공기준 #7(파드 삭제 → 회로 open을 Grafana에서 관측)은 매니페스트에
> `GATEWAY_CIRCUIT_THRESHOLD`를 넣어야 성립한다. 기본 비활성은 stretch 기능의 의도된 설계라
> `k8s/base/`는 그대로 두었다 — 켜는 것은 env 한 줄이고, 언제 켤지는 3주차 관측 스택의 결정이다.

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

**T7이 스크립트로 재현했다(다른 날, 다른 파드).** `HIT=1 / SILENT_MISS=2 / LOUD=9`(전부
`BACKEND_UNAVAILABLE`), 파드별 저장소는 `rows=2 / rows=0 / rows=0`. 비율은 흔들려도 그림은
같다 — 지배적 실패는 시끄럽고, 조용한 소실은 12회 중 2회로 **두 번 다 같은 빈도**로 나왔다.

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

## 조용한 실패 4종 — 화면은 멀쩡한데 내용이 틀리다

이 트랙에서 가장 오래 붙잡힌 것은 깨진 것들이 아니라 **깨졌다고 말하지 않는 것들**이었다.
네 건 다 "에러가 없다"는 점에서 같고, 그래서 넷 다 **찾으러 가야만 보였다.**

| 무엇이 | 화면에 보이는 것 | 실제 | 어떻게 잡혔나 |
|---|---|---|---|
| `sessionAffinity` (§1-A) | `kubectl get svc`가 `ClientIP`를 정상 출력 | 인그레스가 우회해 **아무 일도 안 함** | 경로별 분포를 따로 셌다 (30/0/0 vs 10/10/10) |
| audit (§2) | `/admin`이 **HTTP 200** | 18건 중 6건만 보여줌 | 파드별로 들어가 줄 수를 셌다 |
| ticket 조회 (§5) | `isError` 없는 정상 응답 | **빈 결과** — "없음"과 구별 불가 | 파드별 SQLite의 **테이블 유무**로 판정 |
| rollout (§9) | `successfully rolled out` | 옛 파드가 아직 서빙 중 | 파드 목록에 `Terminating`이 남아 있었다 |

공통점은 **관측 대상과 판정 대상이 어긋나 있다**는 것이다. yaml에 쓴 값, HTTP 상태 코드,
`isError` 플래그, 컨트롤러의 완료 신호 — 넷 다 "설정이 접수됐다"까지만 말하고 "그래서 실제로
그렇게 동작한다"는 말하지 않는다. 그 간극이 K8s에서 유독 넓다.

**반례가 하나 있어서 대비가 선명하다.** `/ready`는 거짓말하지 않았다(§7). ticket-server가
깨졌을 때 `{"ticket": false}`를 정확히 보고했다 — `ensure_session()`으로 "붙은 적 있나"를 묻는
대신 `send_ping()`으로 **왕복을 실제로 시켰기** 때문이다. 조용한 실패를 막는 방법은 상태를
물어보는 게 아니라 **일을 시켜 보는 것**이다.

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

## 9. `kubectl rollout status`는 "옛 파드가 요청을 그만 받는 시점"이 아니다 ✅실측 (T7)

**증상.** `scripts/verify_scaleout.py` 첫 실행이 **`replicas: 1`인데도** `McpError: Session
terminated`로 죽었다. 레플리카가 하나면 세션이 어긋날 파드 자체가 없어야 한다.

```python
kubectl("rollout", "status", "deployment/gateway", "--timeout=180s")   # "successfully rolled out"
# ...곧바로 세션을 연다 → 몇 초 뒤 McpError: Session terminated
```

**원인.** ReplicaSet이 active 파드를 셀 때 `deletionTimestamp`가 찍힌 파드를 **제외한다.** 그래서
옛 파드가 *삭제 표시*되는 순간 `status.replicas == status.updatedReplicas`가 성립하고 rollout이
완료로 보고된다. 그런데 그 파드는 graceful termination 동안 **계속 요청을 받는다** — traefik의
EndpointSlice 반영도 즉시가 아니다. 새 세션이 죽어가는 파드에 열리고, 그 파드가 종료되면 세션이
사라진다. 실제 관측:

```
gateway-558bbb86b8-j5chk   Running       39s   ← rollout status가 완료라고 한 뒤의
gateway-949cb4568-v58fw    Terminating   76s   ← 아직 살아서 서빙 중인 옛 파드
```

**같은 함정이 한 세션에 두 번 걸렸다.** 두 번째는 T12에서 `ops-server`를 `replicas: 0`으로 줄인
직후다. `rollout status`가 즉시 성공을 반환해 "백엔드 사망"으로 알고 호출했는데 결과가 전부
`OK`였다 — 종료 중인 파드가 멀쩡히 응답하고 있었다. **`replicas: 0`에서는 이 함정이 특히
조용하다**: 기다릴 새 파드가 없어 rollout status가 지연 없이 통과한다.

**대응.** 파드 목록이 *준비된 새 파드 정확히 N개*로 정착할 때까지 한 번 더 기다린다
(`verify_scaleout.py`의 `settle()`). 종료 중인 파드도 `kubectl get pods`에는 남으므로 **총 개수가
N인지**를 보면 "옛 파드가 없다"가 판정된다.

> 일반화: K8s의 "완료" 신호는 대체로 **컨트롤러의 장부**를 말하지, 네트워크 경로가 실제로 바뀐
> 시점을 말하지 않는다. §1-A의 `sessionAffinity`(`kubectl`에 보이는데 무동작)와 같은 종류의 거리다.

## 부수 기록 — kustomize가 루트 밖 파일을 거부한다

`configMapGenerator`의 `files:`에 `../../policies/policy.yaml`을 쓰면 실패한다.

```
security; file '...\policies\policy.yaml' is not in or below '...\k8s\base'
```

그래서 ConfigMap 소스를 `k8s/base/config/`에 복사해 뒀다. 사본은 조용히 썩으므로
`tests/unit/test_k8s_config_drift.py`가 원본 5종과의 일치를 고정한다(사본을 한 줄만 바꿔도
실패하는 것을 확인).
