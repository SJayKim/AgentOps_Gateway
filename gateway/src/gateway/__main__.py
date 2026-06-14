"""Gateway 실행 진입점 — `python -m gateway`로 띄운다.

build_app()으로 앱을 조립하고 uvicorn으로 0.0.0.0:8000에 서빙한다. 0.0.0.0 바인딩은
컨테이너 안에서 외부(다른 컨테이너/호스트)의 접속을 받기 위해서다(127.0.0.1이면 컨테이너
내부에서만 보임). 포트 8000은 BACKEND_SPECS의 백엔드 포트(8101~8103)와 겹치지 않는 진입점.
"""

import uvicorn

from gateway.app import build_app

uvicorn.run(build_app(), host="0.0.0.0", port=8000)
