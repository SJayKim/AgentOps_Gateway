#!/bin/bash
# jq 없이 동작 (Windows Git Bash에 jq 미설치). stdin JSON에서 file_path 추출.
INPUT=$(cat)
FILE=$(echo "$INPUT" | sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
if [[ "$FILE" == *.env* ]] || [[ "$FILE" == *secret* ]] || \
   [[ "$FILE" == *.pem ]] || [[ "$FILE" == *.key ]]; then
  echo "Blocked: protected file ($FILE)" >&2
  exit 2
fi
exit 0
