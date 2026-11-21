# ベンチマークの計測（Go）

nginx のアクセスログに `request_time`、アプリからの応答を待った `upstream_time`（秒）、`request_id` を記録します。Go の詳細ログは SQL、セッション取得、画像アップロードの読み取り時間をミリ秒で記録します。SQL の値は記録しません。SQL の時間には接続待ちと結果の読み取りも含まれます。セッション取得の時間にはセッション処理全体が含まれ、Memcached へのアクセスだけの時間ではありません。

画像キャッシュのヒット・ミスも `image_cache` として記録します。画像キャッシュはアプリのメモリ内にあり、`/initialize` とアプリ再起動で消去されます。DB を直接変更する場合はアプリを再起動してキャッシュを消去してください。

詳細ログは大量に出るため、計測自体がベンチマークに影響します。ログを有効にしたスコアと無効にしたスコアは直接比較せず、ディスクの空きにも注意してください。Memcached の個別操作、セッション保存、テンプレートの時間はこの詳細ログには含まれません。

## 有効化

新しい DB を作成した場合、Go の最適化で使用するインデックスを一度だけ適用します。既に適用済みの DB では再実行しないでください。

```bash
for migration in webapp/migrations/*.sql; do
  docker exec -i -e MYSQL_PWD=root private-isu-dev-mysql-1 mysql -uroot isuconp < "$migration"
done
```

WSL 側でリポジトリのルートから実行します。

```bash
docker compose -f .devcontainer/compose.yaml -f .devcontainer/compose.trace.yaml up -d --build app nginx
```

元に戻すには、追加設定を外して再作成します。

```bash
docker compose -f .devcontainer/compose.yaml up -d --force-recreate app
```

## 記録

開発コンテナのターミナルで開始時刻を控え、ベンチマークを実行します。初期化によりデータが変更されます。

```bash
date -u '+%Y-%m-%dT%H:%M:%SZ'
task bench
```

終了後、上で表示された時刻を指定してログを保存します。Docker のログが保持されている範囲を取得します。

```bash
docker logs --since '2026-01-01T00:00:00Z' private-isu-dev-nginx-1 > /tmp/isu-nginx.log 2>&1
docker logs --since '2026-01-01T00:00:00Z' private-isu-dev-app-1 > /tmp/isu-app.log 2>&1
```

時刻は必ず自分の実行時刻に置き換えてください。`/tmp` は一時保存先です。ログ内の同じ `request_id` を探すと、そのリクエストに対応する SQL とファイル読み取りを追えます。nginx のログには URL が含まれるので、共有前に内容を確認してください。

## 集計

同じ計測期間のログから JSON レポートを生成します。

```bash
python3 scripts/analyze-benchmark.py --access /tmp/isu-nginx.log --trace /tmp/isu-app.log --output /tmp/isu-report.json
```

`routes` は URL の種類ごとの回数・応答時間・ステータス、処理別の回数と時間、中央値のリクエストの内訳です。`coverage` で対応付けできなかったログなどを確認できます。JSON の出力先を省略すると標準出力に出ます。別の計測と比べる場合は、異なるファイル名で結果を保存してください。

処理時間は待ち時間を含み、並列処理の場合は重複し得ます。合計時間は CPU 使用時間ではありません。また、すべてのアプリ処理が記録されるわけではありません。

ベンチマーカーは `/initialize` の後に正しさの確認を行い、その後に並行負荷をかけます。ログ全体にはこれらが混在し、開始・終了の境界は自動で記録されません。負荷時だけを分析するには時刻と内容を確認して範囲を選んでください。
