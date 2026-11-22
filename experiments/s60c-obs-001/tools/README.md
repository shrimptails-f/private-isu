# 生データ集計

このディレクトリの親で実行する（Python 3 標準ライブラリのみ）。入力は読み取り専用。

```sh
cd /workspace/private-isu/experiments/s60c-obs-001
python3 tools/observe.py --nginx measure-20261122T040606Z-worktree/logs/run-1-nginx.log --before input/digest-before.tsv --after input/digest-after.tsv
```

nginx は行数、メソッド、メソッド別ルート、ステータスを表示する。数字のパス成分と `/@ユーザー名` を正規化し、クエリ文字列を除く。アクセス形式に一致しない行は非アクセス・未分類として数える。ログに時間情報がないため HTTP 遅延は未測定。digest は `(SCHEMA_NAME, DIGEST)` で突合し、待ち時間の降順で回数、ピコ秒、秒、examined/sent 行を表示する。NULL schema は独立したキーとして含め件数を示す。片側のみのキー、重複、不正値、NULL digest、text 不一致、負差分は報告し、該当キーを確定可能な合計から除く。欠落は eviction、リセット、容量上限などがありゼロとみなせない。待ち時間は並行実行や初期化・管理を含む累積値で経過時間やリクエスト遅延ではない。PREPARE と EXECUTE は独立した処理時間として二重解釈しない。ログ範囲外の処理、アプリ内時間、HTTP 遅延は分からない。

独立検算（同じディレクトリから実行）:

```sh
wc -l measure-20261122T040606Z-worktree/logs/run-1-nginx.log
awk 'match($0, /"[A-Z]+ [^ ]+ HTTP\/[0-9.]+" [0-9][0-9][0-9] /) {a++; s=substr($0,RSTART,RLENGTH); sub(/^.*" /,"",s); c[substr(s,1,3)]++; next} {other++} END {print "access",a,"other",other; for (s in c) print s,c[s]}' measure-20261122T040606Z-worktree/logs/run-1-nginx.log
python3 - <<'PY'
import csv
data = []
for p in ('input/digest-before.tsv', 'input/digest-after.tsv'):
    with open(p) as f:
        rows = list(csv.reader(f, delimiter='\t'))
    data.append({tuple(r[:2]): list(map(int, r[3:7])) for r in rows[1:]})
a, b = data
common = a.keys() & b.keys()
print('keys before, after, common:', len(a), len(b), len(common))
print('count, wait ps, examined, sent:', *[sum(b[k][i]-a[k][i] for k in common) for i in range(4)])
PY
```

今回の結果は全 48,285 行、アクセス 47,295、その他 990、200/302/403/422 は各 44,583/2,277/240/195。digest は前後各 113 キー、共通 113、差分合計は 809,648 回、113,334,452,582,000 ps、examined 100,717,156 行、sent 23,314,381 行。独立検算の TSV 例は今回の重複・不正値がないデータ用で、一般入力の異常検出には集計ツールを使う。
