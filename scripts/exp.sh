#!/usr/bin/env bash
# 性能改善実験の計測を、毎回同じ手順で行うための runner。
# 開発コンテナの中で、リポジトリのルートから実行する。
#
#   scripts/exp.sh deploy  [--ref <git-ref>]       Go アプリをビルドして app コンテナへ配置し、再起動する
#   scripts/exp.sh migrate [--ref <git-ref>]       未適用のマイグレーションを適用し、適用済みを記録する
#   scripts/exp.sh migrate-down <NNN_name>         指定したマイグレーションを *.down.sql で戻す
#   scripts/exp.sh status                          適用済みマイグレーションと app の設定を表示する
#   scripts/exp.sh measure <id> [--ref <git-ref>] [--runs N] [--logs|--raw-logs]
#                                                  deploy・migrate のあと、ベンチマークを N 回実行して記録する
#
# --ref を省略すると作業ツリーの内容を使い、指定するとその commit の内容を使う（作業ツリーは変更しない）。
set -euo pipefail

ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"

APP_CONTAINER=${EXP_APP_CONTAINER:-private-isu-dev-app-1}
MYSQL_CONTAINER=${EXP_MYSQL_CONTAINER:-private-isu-dev-mysql-1}
NGINX_CONTAINER=${EXP_NGINX_CONTAINER:-private-isu-dev-nginx-1}
TARGET=${EXP_TARGET:-http://nginx}
COOLDOWN=${EXP_COOLDOWN:-10}
META_DB=exp_meta
LOCK_FILE="$ROOT/experiments/.measure.lock"

die() { echo "exp: $*" >&2; exit 1; }
log() { echo "exp: $*" >&2; }

mysql_exec() {
  docker exec -i -e MYSQL_PWD=root "$MYSQL_CONTAINER" mysql -uroot --batch --skip-column-names "$@"
}

# --ref のときはその commit を一時ディレクトリへ展開して SRC に設定する。省略時はリポジトリのルート。
SRC=""
SRC_TMP=""
cleanup() { if [[ -n $SRC_TMP ]]; then rm -rf "$SRC_TMP"; fi; }
trap cleanup EXIT

prepare_source() {
  local ref=$1
  if [[ -z $ref ]]; then
    SRC=$ROOT
    return
  fi
  git rev-parse --verify --quiet "$ref^{commit}" >/dev/null || die "commit が見つかりません: $ref"
  SRC_TMP=$(mktemp -d)
  local paths=(webapp/golang)
  if git cat-file -e "$ref:webapp/migrations" 2>/dev/null; then
    paths+=(webapp/migrations)
  fi
  git archive "$ref" "${paths[@]}" | tar -x -C "$SRC_TMP"
  SRC=$SRC_TMP
}

source_label() {
  local ref=$1
  if [[ -z $ref ]]; then
    local head
    head=$(git rev-parse --short HEAD)
    if [[ -n $(git status --porcelain -- webapp/golang webapp/migrations) ]]; then
      echo "worktree@$head+dirty"
    else
      echo "worktree@$head"
    fi
  else
    echo "$ref@$(git rev-parse --short "$ref^{commit}")"
  fi
}

cmd_deploy() {
  local src=$1 bin
  bin=$(mktemp)
  log "Go アプリをビルドします"
  (cd "$src/webapp/golang" && CGO_ENABLED=0 go build -o "$bin" .)
  log "$APP_CONTAINER に配置して再起動します"
  docker stop "$APP_CONTAINER" >/dev/null
  docker cp "$bin" "$APP_CONTAINER:/bin/server"
  docker cp "$src/webapp/golang/templates/." "$APP_CONTAINER:/home/webapp/templates/"
  docker start "$APP_CONTAINER" >/dev/null
  rm -f "$bin"
  wait_ready
}

wait_ready() {
  local i code
  for i in $(seq 1 60); do
    code=$(curl -s -o /dev/null -w '%{http_code}' "$TARGET/login" || true)
    if [[ $code == 200 ]]; then
      return
    fi
    sleep 1
  done
  die "$TARGET/login が 60 秒以内に応答しません"
}

ensure_meta() {
  mysql_exec -e "CREATE DATABASE IF NOT EXISTS $META_DB; CREATE TABLE IF NOT EXISTS $META_DB.migrations (name VARCHAR(255) PRIMARY KEY, applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
}

applied_migrations() {
  mysql_exec -e "SELECT name FROM $META_DB.migrations ORDER BY name"
}

source_migrations() {
  local src=$1 f
  for f in "$src"/webapp/migrations/*.sql; do
    [[ -e $f ]] || continue
    [[ $f == *.down.sql ]] && continue
    basename "$f" .sql
  done | sort
}

# 計測する source に含まれるマイグレーションだけが適用された状態にする。
# source にないマイグレーションが適用済みなら、黙って戻さずに止める（戻すかどうかは呼び出し側が決める）。
cmd_migrate() {
  local src=$1 name out extra
  ensure_meta
  extra=$(comm -13 <(source_migrations "$src") <(applied_migrations))
  if [[ -n $extra ]]; then
    die "計測する内容にないマイグレーションが適用済みです: $(echo "$extra" | tr '\n' ' ')。scripts/exp.sh migrate-down <name> で戻してください"
  fi
  for name in $(comm -23 <(source_migrations "$src") <(applied_migrations)); do
    log "マイグレーションを適用します: $name"
    if ! out=$(mysql_exec isuconp < "$src/webapp/migrations/$name.sql" 2>&1); then
      # 記録を始める前に手で適用済みだったインデックスは、適用済みとして記録する
      if grep -q 'ERROR 1061' <<<"$out"; then
        log "$name は既に適用されていたため、適用済みとして記録します"
      else
        echo "$out" >&2
        die "マイグレーションに失敗しました: $name"
      fi
    fi
    mysql_exec -e "INSERT INTO $META_DB.migrations (name) VALUES ('$name')"
  done
}

cmd_migrate_down() {
  local name=${1:-} file
  [[ -n $name ]] || die "usage: scripts/exp.sh migrate-down <NNN_name>"
  name=${name%.sql}
  file="$ROOT/webapp/migrations/$name.down.sql"
  [[ -f $file ]] || die "$file がありません"
  ensure_meta
  applied_migrations | grep -qx "$name" || die "$name は適用されていません"
  log "マイグレーションを戻します: $name"
  mysql_exec isuconp < "$file"
  mysql_exec -e "DELETE FROM $META_DB.migrations WHERE name = '$name'"
}

trace_enabled() {
  if docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$APP_CONTAINER" | grep -qx 'ISUCONP_TRACE=1'; then
    echo true
  else
    echo false
  fi
}

cmd_status() {
  ensure_meta
  echo "applied migrations:"
  applied_migrations | sed 's/^/  /'
  echo "trace: $(trace_enabled)"
}

json_field() {
  # benchmarker の 1 行の JSON から数値・真偽値のフィールドを取り出す
  sed -nE "s/.*\"$2\":(true|false|-?[0-9]+).*/\1/p" "$1"
}

cmd_measure() {
  local id=${1:-}
  shift || true
  [[ $id =~ ^[A-Za-z0-9._-]+$ ]] || die "usage: scripts/exp.sh measure <id> [--ref <git-ref>] [--runs N] [--logs|--raw-logs]"
  local ref="" runs=3 logs=false analyze=false
  while [[ $# -gt 0 ]]; do
    case $1 in
      --ref) ref=$2; shift 2 ;;
      --runs) runs=$2; shift 2 ;;
      --logs) logs=true; analyze=true; shift ;;
      --raw-logs) logs=true; shift ;;
      *) die "unknown option: $1" ;;
    esac
  done
  [[ $runs =~ ^[1-9][0-9]*$ ]] || die "--runs は 1 以上の整数で指定してください"

  mkdir -p "$ROOT/experiments"
  exec 9>"$LOCK_FILE"
  flock -n 9 || die "別の計測が実行中です"

  local label out_dir
  prepare_source "$ref"
  label=$(source_label "$ref")
  if [[ -z $ref ]]; then
    out_dir="$ROOT/experiments/$id/measure-$(date -u +%Y%m%dT%H%M%SZ)-worktree"
  else
    out_dir="$ROOT/experiments/$id/measure-$(date -u +%Y%m%dT%H%M%SZ)-$(git rev-parse --short "$ref^{commit}")"
  fi
  mkdir -p "$out_dir"

  log "benchmarker をビルドします"
  (cd "$ROOT/benchmarker" && make -s)

  cmd_migrate "$SRC"
  cmd_deploy "$SRC"

  {
    echo "{"
    echo "  \"id\": \"$id\","
    echo "  \"source\": \"$label\","
    echo "  \"trace\": $(trace_enabled),"
    echo "  \"runs\": $runs,"
    echo "  \"cooldownSeconds\": $COOLDOWN,"
    echo "  \"nproc\": $(nproc),"
    echo "  \"loadavgBefore\": \"$(cut -d' ' -f1-3 /proc/loadavg)\","
    echo "  \"migrations\": [$(applied_migrations | sed 's/.*/"&"/' | paste -sd, -)],"
    echo "  \"startedAt\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\""
    echo "}"
  } >"$out_dir/meta.json"

  local i started
  for i in $(seq 1 "$runs"); do
    if [[ $i -gt 1 ]]; then
      sleep "$COOLDOWN"
    fi
    docker restart "$APP_CONTAINER" >/dev/null
    wait_ready
    started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    log "ベンチマーク $i/$runs"
    (cd "$ROOT/benchmarker" && ./bin/benchmarker -t "$TARGET" -u ./userdata) \
      >"$out_dir/run-$i.json" 2>"$out_dir/run-$i.stderr.log" || true
    echo "$started $(cut -d' ' -f1-3 /proc/loadavg)" >"$out_dir/run-$i.loadavg"
    if $logs; then
      mkdir -p "$out_dir/logs"
      docker logs --since "$started" "$NGINX_CONTAINER" >"$out_dir/logs/run-$i-nginx.log" 2>&1
      docker logs --since "$started" "$APP_CONTAINER" >"$out_dir/logs/run-$i-app.log" 2>&1
      if $analyze && command -v python3 >/dev/null; then
        python3 "$ROOT/scripts/analyze-benchmark.py" --access "$out_dir/logs/run-$i-nginx.log" \
          --trace "$out_dir/logs/run-$i-app.log" --output "$out_dir/run-$i-report.json" || true
      fi
    fi
  done

  summarize "$out_dir" "$runs" >"$out_dir/summary.json"
  cat "$out_dir/summary.json"
  echo "summary: ${out_dir#"$ROOT"/}/summary.json"
}

summarize() {
  local dir=$1 runs=$2 i pass score scores=() all_pass=true
  for i in $(seq 1 "$runs"); do
    pass=$(json_field "$dir/run-$i.json" pass)
    score=$(json_field "$dir/run-$i.json" score)
    [[ $pass == true ]] || all_pass=false
    scores+=("${score:-0}")
  done
  local sorted median min max
  sorted=$(printf '%s\n' "${scores[@]}" | sort -n)
  min=$(head -1 <<<"$sorted")
  max=$(tail -1 <<<"$sorted")
  median=$(awk '{a[NR]=$1} END {if (NR%2) print a[(NR+1)/2]; else print int((a[NR/2]+a[NR/2+1])/2)}' <<<"$sorted")
  echo "{\"allPass\": $all_pass, \"median\": $median, \"min\": $min, \"max\": $max, \"scores\": [$(IFS=,; echo "${scores[*]}")]}"
}

main() {
  local cmd=${1:-}
  shift || true
  local ref=""
  case $cmd in
    deploy|migrate)
      if [[ ${1:-} == --ref ]]; then ref=${2:?--ref には git ref を指定してください}; fi
      prepare_source "$ref"
      "cmd_$cmd" "$SRC"
      ;;
    migrate-down) cmd_migrate_down "$@" ;;
    status) cmd_status ;;
    measure) cmd_measure "$@" ;;
    *) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2 ;;
  esac
}

main "$@"
