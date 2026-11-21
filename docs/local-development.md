# ローカル開発 (Go)

WSL の `/home/user/git_wsl/private-isu` を VS Code で開き、Dev Containers: Reopen in Container を選択します。開発コンテナとアプリのビルドに Go 1.27.4 を使用します。Go と各イメージは定期的に更新してください。初期実装と依存関係には演習用の古い箇所や脆弱性が残り得るため、公開サーバーとして使わないでください。

1. コンテナのターミナルで `task init` を実行します。公式初期データは圧縮状態で合計約 2.4 GB あり、展開にも空き容量と時間が必要です。中断後の再実行でダウンロードを再開します。完了前に MySQL を起動しないでください。
2. WSL 側で `docker compose -f .devcontainer/compose.yaml up -d --build nginx` を実行します。初回の MySQL インポートには時間がかかります。
3. ブラウザーで http://localhost:8080 を開きます。コンテナ内で `task bench` を実行すると公式ベンチマーカーを実行します。ベンチマークはアプリのデータを初期化します。

Go アプリを変更したら WSL 側で `docker compose -f .devcontainer/compose.yaml up -d --build app nginx` を実行します。開発コンテナの Dockerfile やマウントを変更した場合は VS Code の Dev Containers: Rebuild Container を使います。停止は WSL 側で `docker compose -f .devcontainer/compose.yaml down`。DB は名前付きボリュームに残ります。初期データのインポートは空の DB ボリュームの初回起動時だけ行われます。誤って初期データのない状態で起動した場合、既存 DB を消さずに調査してください。

`task codex` と `task claude` は承認やサンドボックスを省略する設定です。WSL の `~/.codex` と `~/.claude` を書き込み可能で共有するため、認証だけでなく設定・履歴なども共有され、コンテナからホスト側を変更できます。Claude の `~/.claude.json` は共有していないため初回設定が必要になることがあります。`~/.config/gh` は読み取り専用で共有しますが、GitHub API の権限はトークンに従います。`ghp` と `ghw` の使い分けは [GitHub CLI 利用手順](github-cli.md) を参照。非対話 Bash では `source scripts/gh-wrappers.bash` が必要です。

Docker の `desktop.exe` 認証ヘルパーが利用できず公開イメージの取得で失敗する場合、WSL 側で `mkdir -p /tmp/private-isu-docker-config && export DOCKER_CONFIG=/tmp/private-isu-docker-config` を実行した同じシェルから Docker や `code .` を起動すると、空の一時設定で公開イメージを取得できます。この設定にはプライベートレジストリの認証はありません。
