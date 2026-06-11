import uvicorn

from gateway.app import build_app

uvicorn.run(build_app(), host="0.0.0.0", port=8000)
