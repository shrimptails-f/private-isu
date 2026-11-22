# 観測ツール

Python 3 標準ライブラリのみ使用。リポジトリルートから実行する。

    python3 experiments/s60d-obs-002/tools/access_summary.py experiments/s60d-obs-002/measure-20261122T050914Z-worktree/logs/run-1-nginx.log > /tmp/access-summary.json
    python3 -m unittest discover -s experiments/s60d-obs-002/tools -v

集計は access、warn、その他を分け、メソッド、ステータス、クエリを除いたパスを JSON で出す。入力は読み取りのみ。パス自体にも秘密が含まれる場合は出力の取り扱いに注意する。

親計測の直前に以下を開始し、計測全体を含む十分な duration を指定する。終了時に JSON が標準出力に出る。duration 0 は SIGINT または SIGTERM まで実行する。docker の起動遅延を考慮して親計測前にサンプラーが動いていることを確認する。

    docker run --rm -i --pid=container:private-isu-dev-app-1 private-isu-dev-dev python3 - --interval 0.1 --duration 60 < experiments/s60d-obs-002/tools/cpu_sample.py > /tmp/cpu-summary.json

対象 app コンテナの実行、Docker と共有 PID namespace の利用権限、対象 `/proc/<pid>/stat` と `comm` の読み取り権限が必要。異なる namespace や権限では対象が見えない場合がある。errors は消滅、権限拒否、パース失敗、その他の読み取り失敗、列挙失敗、負差分を区別する。欠測はゼロ CPU ではない。列挙失敗時も結果は部分的である。

同一 (PID, starttime) が連続して見えた区間だけ utime + stime の差分を tick 周波数で秒に換算する。各 PID の平均はその観測区間の合計で割り、1 コアを 100% とするため 100% を超え得る。comm ごとは区間末の名前に CPU 秒を割り当て、改名や同名プロセスを合算する。子の累積 CPU は加えず子 PID を別に数える。初回・最終の端数、標本間に生まれて消えたプロセス、最後の標本以降や読み取り欠落区間は捕捉できず下限的な推定となる。標本間隔と読み取り時間は一定ではなく、サンプラー自体も CPU を使い、同じ namespace 内では集計対象に含まれる。観測負荷と短命プロセスの捕捉率は親計測で判断する。cmdline と環境は読まない。
