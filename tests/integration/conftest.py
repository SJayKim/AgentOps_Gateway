"""통합 테스트용 백엔드 3종 subprocess 픽스처.

[왜 진짜 subprocess로 띄우나]
단위 테스트는 함수를 직접 부르지만, 통합 테스트는 Gateway가 '실제 HTTP'로 백엔드에 붙는
경로(집계·라우팅·재연결)를 검증해야 한다. 그래서 백엔드를 별도 프로세스로 진짜 기동한다.

[왜 module 스코프인가]
프로세스 기동은 비싸므로 모듈 단위로 한 번만 띄워 재사용한다. 동시에, 백엔드를 죽였다
살리는(kill/재기동) 테스트가 다른 모듈에 영향 주지 않도록 격리 경계도 모듈에 둔다 —
한 모듈이 백엔드를 망가뜨려도 다음 모듈은 깨끗한 픽스처로 다시 시작한다.
"""

import pytest
from helpers import BackendProc


@pytest.fixture(scope="module")
def backends(tmp_path_factory):
    # ticket DB만 임시 파일 경로를 주입 — 테스트 간 데이터가 레포/서로를 오염시키지 않게.
    db = tmp_path_factory.mktemp("gw") / "tickets.db"
    procs = {
        "ticket": BackendProc("ticket", {"TICKET_DB_PATH": str(db)}),
        "docs": BackendProc("docs", {}),
        "ops": BackendProc("ops", {}),
    }
    for p in procs.values():
        p.start()  # start()는 포트가 실제로 열릴 때까지 블록한다(helpers.wait_port)
    yield procs  # ← 테스트들이 이 백엔드 핸들로 kill/재기동까지 제어한다
    for p in procs.values():
        p.stop()  # 모듈 종료 시 전부 정리 + 포트가 비워질 때까지 대기
