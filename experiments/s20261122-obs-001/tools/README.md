# nginx 生ログ集計

リポジトリルートから、Python 3 の標準ライブラリだけで実行する。

```sh
LOG=experiments/s20261122-obs-001/measure-20261121T153638Z-worktree/logs/run-1-nginx.log
python3 experiments/s20261122-obs-001/tools/analyze_nginx.py "$LOG" > /tmp/nginx-summary.json
```

combined 形式（末尾の引用された forwarded-for フィールドは任意）をアクセス行として扱う。nginx の日時と重大度で始まる診断行、それ以外の未解析行は別計数する。アクセス全体を対象に、ステータス、User-Agent、メソッドとパスごとの件数、レスポンスボディの bytes を JSON で出力する。パスはクエリを除き、数値 ID の `/image/123.jpg` 等を `/image/:id.jpg`、`/posts/123` を `/posts/:id` に変換する。他のパスはそのまま残す。キーとルートは安定した順序で出力する。

ボディサイズ `-` は bytes 合計に加えず、全体とルート別の `missing_body_bytes` に数える。ステータスや User-Agent の `-` はそのまま内訳に残す。解釈できないリクエストは `<unparsed>` としてまとめ、`unparsed_request` に数える。不正な時刻は `invalid_timestamp` に数え、時刻範囲から除外する。日時は秒単位の記録時刻で、所要時間ではない。このログには所要時間や upstream 時間がなく、空の app log から SQL やアプリ内の時間も判定できない。診断行はアクセス行や所要時間として数えない。想定外の形式は未解析として可視化されるため、別のログ形式に使う場合は未解析件数を確認する。

## 独立検算

上記の `LOG` を設定した上で、元ログから以下を実行する（この入力固有の形式に基づく）。

```sh
wc -l "$LOG"
awk 'substr($1,1,4)!="2026" {n++; s[$9]++} END {print "access",n; for (x in s) print x,s[x]}' "$LOG"
awk '$6 == "\"GET" && $7 ~ /^\/image\/[0-9]+\.jpg(\?.*)?$/ {n++} END {print "jpg", n}' "$LOG"
python3 -c 'import json; d=json.load(open("/tmp/nginx-summary.json")); r=d["routes"]; assert d["lines"]==d["access"]+d["diagnostic"]+d["unparsed"]; assert sum(x["count"] for x in r)==d["access"]==sum(d["status"].values())==sum(d["user_agent"].values()); assert sum(x["body_bytes"] for x in r)==d["body_bytes"]; assert sum(x["missing_body_bytes"] for x in r)==d["missing_body_bytes"]; print("aggregate invariants: OK")'
```

今回の値は全 11,143 行、アクセス 10,945、診断 198、未解析 0、ステータス 200: 10,611、302: 295、403: 20、422: 19、GET JPEG 4,623 件。ボディサイズ合計は 1,763,975,662 bytes。欠損サイズ、不正時刻、未解析リクエストはいずれも 0。
