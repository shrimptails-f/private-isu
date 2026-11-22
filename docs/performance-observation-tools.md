# 自作観測ツールの記録

今回の性能改善では、各セッションの生データを入力として観測ツールを新規作成し、Phasekeeper の検証後に親側でも再実行・代表値の元データ照合を行った。改善の経過と採否は [性能改善実験の記録](performance-experiments.md) を参照。

ツールのソースは `experiments/` 以下に配置している。これらはアプリの本番機能ではない。依頼・計測結果・生ログはローカルに残しており、このブランチには含まれないため、再生成例は同じローカル入力がある環境でのみ実行できる。セッションを新規開始するときは、isu-director の指示に従い過去のツールを転用せず、そのセッションの生データから新たに観測を用意する。

## 入力と解釈上の注意

- nginx の raw log はアクセス行と警告・未分類行を含む。アクセス数と全行数は異なる。アクセスパスにも情報が含まれ得るため、ログや出力を共有する前に確認する。
- 今回の nginx ログにはリクエスト時間・upstream 時間がなく、HTTP レイテンシは分からない。空の app log は観測できなかったことを示し、アプリ内処理時間やエラーがゼロという意味ではない。
- MySQL digest の差分は、ベンチ前後の `performance_schema.events_statements_summary_by_digest` の読み取り値から算出した。フィールドは `SCHEMA_NAME`、`DIGEST`、`DIGEST_TEXT`、`COUNT_STAR`、`SUM_TIMER_WAIT`、`SUM_ROWS_EXAMINED`、`SUM_ROWS_SENT`。時間単位はピコ秒で、秒にするには `10^12` で割る。
- SQL 時間は並行処理を含む累積で、壁時計時間ではない。取得窓には初期化・管理処理も含まれる。digest の欠落、リセット、サンプリング境界、読み取り中の更新に注意する。PREPARE・EXECUTE・DEALLOCATE は別イベントで、足し合わせた件数を SQL 呼び出し回数とみなさない。
- 実 DB との比較ツールは読み取り専用だが、接続先の状態に依存する。結果は測定時点の一部の入力に対する証拠であり、障害・並行更新・すべての入力の互換性を証明しない。
- 計測 runner、アプリ、DB、Docker の設定を変更せずに実行できる集計方法を以下に示す。ベンチマークを実行する場合は `task exp:measure` を使い、複数のベンチを並行起動しない。

## 初回セッションのツール

### nginx ログ集計

[`analyze_nginx.py`](../experiments/s20261122-obs-001/tools/analyze_nginx.py) はアクセス・診断・未解析行、status、User-Agent、正規化した method/path、レスポンス body bytes を JSON に集計する。画像 ID と投稿 ID を正規化する。欠損サイズは別に数え、日時は記録時刻であって所要時間ではない。

```sh
python3 experiments/s20261122-obs-001/tools/analyze_nginx.py \
  experiments/s20261122-obs-001/measure-20261121T153638Z-worktree/logs/run-1-nginx.log \
  > /tmp/isu-nginx-summary.json
```

今回の結果は全 11,143 行、アクセス 10,945、診断 198、未解析 0。GET JPEG 4,623 件は元ログからも独立に照合した。詳細な検算コマンドは [ツール README](../experiments/s20261122-obs-001/tools/README.md)。

### digest 差分集計

[`digest_diff.py`](../experiments/s20261122-obs-002/tools/digest_diff.py) は `(schema, digest)` で TSV を突き合わせ、SQL 件数・累積時間・調査行数・送信行数の差を出力する。after のみの行は before をゼロと仮定して明示し、消失・逆行・文の相違は診断する。この仮定は以前に存在しなかったことの証明ではない。

```sh
python3 experiments/s20261122-obs-002/tools/digest_diff.py \
  experiments/s20261122-obs-002/input/digest-before.tsv \
  experiments/s20261122-obs-002/input/digest-after.tsv /tmp/isu-digest-diff.json
```

今回の共通キーにカウンター逆行はなかった。コメント取得 LIMIT 8,206 回・累積 426.625 秒などを元 TSV と独立に照合した。詳細は [ツール README](../experiments/s20261122-obs-002/tools/README.md)。

## 60,000 点を目標とした各セッションのツール

| セッション・ツール | 役割と主な出力 |
|---|---|
| [`s60a/observe.py`](../experiments/s60a-obs-001/tools/observe.py) | nginx の status・bytes・時刻別件数と digest 前後差を JSON 化。今回の 47,421 行、アクセス 47,023 を独立照合 |
| [`s60b/aggregate.py`](../experiments/s60b-obs-001/tools/aggregate.py) | nginx 集計と digest 差分をテキストで出力。今回の 47,195 行・アクセス 46,799 を独立照合 |
| [`s60b/compare.go`](../experiments/s60b-obs-001/tools/compare.go) | 同じ読み取り専用スナップショットで users の準備済み SELECT と整数リテラル SELECT の全列等を比較。8 ID で一致。出力には行の値が含まれ得る |
| [`s60c/observe.py`](../experiments/s60c-obs-001/tools/observe.py) | nginx の正規化ルート・status と digest 差分を集計。今回の 48,285 行・アクセス 47,295 を照合 |
| [`s60c/digest.py`](../experiments/s60c-obs-002/tools/digest.py) | digest の SQL 種別別・総計差分と検算を出す。不完全なキーや負差分等を検出 |
| [`s60c/idcompare`](../experiments/s60c-obs-002/tools/idcompare/main.go) | 読み取り専用 snapshot の整数 ID 検索を比較。既存・欠損・整数境界を含む8分類が一致。ID や行データ自体は表示しない |
| [`s60d/summarize.py`](../experiments/s60d-obs-001/tools/summarize.py) | nginx・app ログと digest 前後差を集計。app ログが空の場合は観測欠損と明記 |
| [`s60d/process_sample.py`](../experiments/s60d-obs-001/tools/process_sample.py) | `/proc` の stat と comm から指定 PID の子孫を標本化。プロセス名・PID/starttime 別の捕捉数を出す |
| [`s60d/access_summary.py`](../experiments/s60d-obs-002/tools/access_summary.py) | nginx のアクセス・警告・未分類行、method・status・path を JSON 化 |
| [`s60d/cpu_sample.py`](../experiments/s60d-obs-002/tools/cpu_sample.py) | `/proc` の stat と comm を標本化し、連続して見えた PID/starttime の CPU tick 差分を集計 |
| [`s60e/observe.py`](../experiments/s60e-obs-001/tools/observe.py) | nginx と digest 前後差を JSON 化。PREPARE・EXECUTE・DEALLOCATE を別イベントとして扱う |

### ログ・digest の再生成例

リポジトリルートで実行する。入力ログ・TSV は読み取り専用、出力は `/tmp` に置く。

```sh
python3 experiments/s60a-obs-001/tools/observe.py \
  --nginx experiments/s60a-obs-001/measure-20261121T163421Z-worktree/logs/run-1-nginx.log \
  --before experiments/s60a-obs-001/input/digest-before.tsv \
  --after experiments/s60a-obs-001/input/digest-after.tsv > /tmp/s60a.json

python3 experiments/s60b-obs-001/tools/aggregate.py \
  experiments/s60b-obs-001/measure-20261121T172003Z-worktree/logs/run-1-nginx.log \
  experiments/s60b-obs-001/input/digest-before.tsv \
  experiments/s60b-obs-001/input/digest-after.tsv > /tmp/s60b.txt

python3 experiments/s60c-obs-001/tools/observe.py \
  --nginx experiments/s60c-obs-001/measure-20261122T040606Z-worktree/logs/run-1-nginx.log \
  --before experiments/s60c-obs-001/input/digest-before.tsv \
  --after experiments/s60c-obs-001/input/digest-after.tsv > /tmp/s60c.txt

python3 experiments/s60c-obs-002/tools/digest.py \
  experiments/s60c-obs-002/input/digest-before.tsv \
  experiments/s60c-obs-002/input/digest-after.tsv > /tmp/s60c-digest.tsv

python3 experiments/s60d-obs-001/tools/summarize.py \
  --nginx experiments/s60d-obs-001/measure-20261122T045012Z-worktree/logs/run-1-nginx.log \
  --app experiments/s60d-obs-001/measure-20261122T045012Z-worktree/logs/run-1-app.log \
  --before experiments/s60d-obs-001/input/digest-before.tsv \
  --after experiments/s60d-obs-001/input/digest-after.tsv > /tmp/s60d.json

python3 experiments/s60d-obs-002/tools/access_summary.py \
  experiments/s60d-obs-002/measure-20261122T050914Z-worktree/logs/run-1-nginx.log > /tmp/s60d-access.json

python3 experiments/s60e-obs-001/tools/observe.py \
  --access experiments/s60e-obs-001/measure-20261122T052215Z-worktree/logs/run-1-nginx.log \
  --before experiments/s60e-obs-001/input/digest-before.tsv \
  --after experiments/s60e-obs-001/input/digest-after.tsv > /tmp/s60e.json
```

`s60c/digest.py` の標準エラーには種別別・総計と検算が出る。`s60c` と `s60e` の集計上の欠損や仮定、独立検算の詳細は、それぞれの [s60c README](../experiments/s60c-obs-001/tools/README.md)、[s60c digest README](../experiments/s60c-obs-002/tools/README.md)、[s60e README](../experiments/s60e-obs-001/tools/README.md) を参照。

### 整数 ID の読み取り比較

`s60b/compare.go` と `s60c/idcompare` は、それぞれ別に作成したツール。準備済み SELECT と整数リテラル SELECT を読み取り専用トランザクション内で比較する。実 DB 接続が必要で、接続先・認証は実行環境に合わせて環境変数で設定する。資格情報をログ・共有ファイル・シェル履歴に残さない。`s60b` の出力には users の列値が含まれ得るため取り扱いに注意する。

`s60c/idcompare` のビルド・実行手順、入力の制約と未検証範囲は [README](../experiments/s60c-obs-002/tools/README.md) にある。実測では既存、欠損、0、負、INT 境界および範囲外の8分類で一致した。

### プロセスと CPU の標本

両ツールとも `cmdline` や環境変数を読まず、`/proc` の stat と comm を使用する。短命プロセスは標本間に終了し得るため、捕捉数は起動総数ではない。CPU サンプラーは連続して見えた区間の `utime + stime` 差分だけを集計し、子プロセスの累積 CPU を親に二重加算しない。端数・消滅・権限・PID namespace・観測処理自体の CPU/I/O による欠落や負荷がある。

今回の PID namespace 共有を使った CPU 標本の再現例は次のとおり。実行環境で該当コンテナと Python が必要。実負荷中に使う場合は監視開始・終了とアプリ再起動のタイミングにも注意する。

```sh
docker run --rm -i --pid=container:private-isu-dev-app-1 \
  --entrypoint python3 private-isu-dev-dev - --interval 0.1 --duration 65 \
  < experiments/s60d-obs-002/tools/cpu_sample.py > /tmp/isu-cpu-sample.json
```

今回の負荷中の標本は 646 回、サーバの捕捉区間は 31.79 CPU 秒、サンプラーは 0.45 CPU 秒、消滅による読み取り失敗は12件。短命の `openssl` 等は連続標本を得られず CPU 時間を測れなかった。別のプロセス標本では `openssl`・`sed` を各45個体捕捉したが、実行総数ではない。詳細な実装・制約・テストは [CPU サンプラー README](../experiments/s60d-obs-002/tools/README.md) と ローカルの `experiments/s60d/cpu-sampling.json` を参照。
