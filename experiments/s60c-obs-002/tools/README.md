# SQL 観測ツール

実行は `/workspace/private-isu` から。入力 TSV は読み取り専用。集計結果には SQL とスキーマ名が含まれるため、共有前に内容を確認する。

```sh
python3 experiments/s60c-obs-002/tools/digest.py experiments/s60c-obs-002/input/digest-before.tsv experiments/s60c-obs-002/input/digest-after.tsv > /tmp/digest-delta.tsv 2> /tmp/digest-summary.txt
```

ヘッダー名とキーを検証し、共通キーは after − before、after のみは before=0 と明示して計上する。前のみ、重複、NULL/空 digest、不正値、文の相違、負差分は不完全としてエラー終了する。不完全な場合の部分集計は全体の代表値ではない。標準出力は行別、標準エラーは種別別と総計、種別別合計の検算。カウンタは件数、時間はピコ秒と秒、行数は examined/sent。PREPARE、EXECUTE、DEALLOCATE は別イベントであり合算を実行回数としない。digest の正規化、上限、取得窓、並行処理、計測外の文による欠落があり、累積時間は経過時間ではない。初期化・管理も含む。

今回の独立検算は各 TSV の各カウンタ列をそれぞれ全行合計して差し引く方法で、806646、113625015215000 ps（113.625015215 秒）、100623892、23085659 と一致。種別別合計とも一致。両入力のキー集合が一致し、異常は検出されなかった。

```sh
cd experiments/s60c-obs-002/tools/idcompare
go build -o /tmp/s60c-idcompare .
ISUCONP_DB_HOST=<host> ISUCONP_DB_PORT=<port> ISUCONP_DB_USER=<user> ISUCONP_DB_NAME=<database> ISUCONP_DB_PASSWORD=<password> /tmp/s60c-idcompare
```

接続値は環境変数で渡す。既定値はアプリと同じ localhost:3306、root、空パスワード、isuconp、utf8mb4。資格情報をコマンド履歴などに残さないこと。単一接続の read-only REPEATABLE READ トランザクションの最初の読み取りでスナップショットを確立し、その中で users の既存・欠損 ID を選び、固定境界値とともに明示的 prepare と int64 の10進リテラルを比較する。列名、行数、各値と NULL を比較し、両方エラーでも未確認として失敗する。結果はカテゴリと一致状態のみで、ID や行データは表示しない。存在・欠損を選べない場合や接続・権限・検索エラーは未確認。現在の DB と users のみの検証で、計測時の DB、他テーブル、並行更新の挙動は未確認。
