# certs/

로컬 전용, git 미추적. TLS 검사 소프트웨어(AV 등)가 있는 머신에서 도커 빌드가
`invalid peer certificate: UnknownIssuer`로 실패하면, 신뢰할 루트 CA를 `*.crt`
(PEM)로 여기 두면 빌드 시 컨테이너 신뢰 저장소에 추가된다. 없으면 no-op.
