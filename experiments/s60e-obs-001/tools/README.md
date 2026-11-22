# 現行版の観測集計

リポジトリルートから Python 3 で実行する。入力は読み取り専用、JSON は標準出力。

```sh
python3 experiments/s60e-obs-001/tools/observe.py \
  --access experiments/s60e-obs-001/measure-20261122T052215Z-worktree/logs/run-1-nginx.log \
  --before experiments/s60e-obs-001/input/digest-before.tsv \
  --after experiments/s60e-obs-001/input/digest-after.tsv > /tmp/s60e-obs-001.json
```

アクセスは combined 形式のリクエスト行を method、クエリ文字列を除く生 path、status で集計する。動的 ID は正規化しない。非アクセス行とアクセス候補の解析失敗を別に数え、行数と status の合計を検査する。候補は nginx のアドレス等、角括弧内の日時、引用符の開始を持つ行とする。それ以外の不正行は非アクセスに含まれる。request_time がないためレイテンシは算出しない。

TSV はヘッダ名で読み、schema と digest のキーで対応させる。両方に一意に存在する行のみ整数差分を出し、追加・消失・重複・NULL digest・SQL テキスト相違・負差分を anomalies に列挙する。未対応行をゼロ埋めしない。差分の COUNT_STAR はイベント件数、SUM_TIMER_WAIT はピコ秒で、wait_seconds は 10^12 で割った秒、行数は rows examined/sent。件数順と累積待機時間順の全行を出す。イベント種別は SQL テキスト先頭語で、PREPARE / EXECUTE / DEALLOCATE は別イベントとして計上する。接頭語を除いたテキストと schema の一致による比較は参考であり、欠けたイベントと EXECUTE−PREPARE の件数差を示すが、因果関係や一対一対応は証明しない。

時間は並行実行、初期化、管理処理を含むサーバ全体の累積で壁時計時間ではない。統計のリセットや収集境界はこの入力だけでは証明できない。正規化済み digest からバインド値やアクセスとの対応は分からない。アプリログが空のためアプリ内部の内訳も不明。入力の不正なヘッダ・数値はエラーとして終了する。
