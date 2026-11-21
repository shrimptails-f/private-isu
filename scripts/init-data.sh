#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
base=https://github.com/catatsuy/private-isu/releases/download/img
fetch() {
  local target=$1 name=$2
  if [[ ! -f "$target" ]]; then
    curl -fL --retry 3 -C - -o "$target.part" "$base/$name"
    mv "$target.part" "$target"
  fi
}
fetch webapp/sql/dump.sql.bz2 dump.sql.bz2
if [[ ! -f benchmarker/userdata/.img-ready ]]; then
  fetch benchmarker/userdata/img.zip img.zip
  unzip -qo benchmarker/userdata/img.zip -d benchmarker/userdata
  touch benchmarker/userdata/.img-ready
fi
