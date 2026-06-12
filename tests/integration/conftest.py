"""백엔드 3종 subprocess 픽스처 — module 스코프 (kill/재기동 테스트 격리)."""

import pytest
from helpers import BackendProc


@pytest.fixture(scope="module")
def backends(tmp_path_factory):
    db = tmp_path_factory.mktemp("gw") / "tickets.db"
    procs = {
        "ticket": BackendProc("ticket", {"TICKET_DB_PATH": str(db)}),
        "docs": BackendProc("docs", {}),
        "ops": BackendProc("ops", {}),
    }
    for p in procs.values():
        p.start()
    yield procs
    for p in procs.values():
        p.stop()
