## Render へデプロイするための詳細手順（日本語）

この文書は、リポジトリ内の `render.yaml` と既存の Dockerfile を使って、フルスタック（フロントエンド + バックエンド + Render のマネージド Postgres）を Render.com にデプロイするための詳細手順です。順番に実行すれば動作確認まで進められるように書いてあります。

前提（事前準備）
- GitHub にこのリポジトリがあること（`main` ブランチにデプロイしたいコードが入っている）。
- Render アカウントを作成済みであること。
- `backend/entrypoint.sh` が実行権限あり（`chmod +x backend/entrypoint.sh`）。
- `backend/requirements.txt` に `gunicorn` が含まれている（このリポジトリでは既に追加済み）。

1) リポジトリを Render に接続
- Render ダッシュボードで "New" → "Web Service" → "Connect a repository" を選択し、GitHub 連携でこのリポジトリを選びます。
- リポジトリルートに `render.yaml` がある場合、Render はそれを読んでサービスを自動的に作成できます。手動で作る場合は次のサービスを作成してください。
   - Backend（Docker）: Dockerfile パスは `backend/Dockerfile` を指定。
   - Frontend（Docker）: Dockerfile パスは `frontend/Dockerfile` を指定。
   - Managed Postgres（Database）: 名前は任意（例: `fanart-viewer-db`）。

2) サービスごとの環境変数・シークレットを設定
（Render ダッシュボード > Service > Environment）
- Backend に最低限設定するもの:
   - `DJANGO_SECRET_KEY`（secret）: 安全なランダム文字列を設定（例は下に生成コマンドあり）。
   - `DJANGO_DEBUG` = `0`
   - `GUNICORN_WORKERS` = `3`（任意）
   - `GUNICORN_THREADS` = `4`（任意）
   - DB 接続情報:
      - Render のマネージド DB を使う場合、Render が `DATABASE_URL` を自動で注入します。しかしこのプロジェクトの `backend/settings.py` は現在 `POSTGRES_*` 系の環境変数（`POSTGRES_USER` 等）を参照します。以下のどちらかを行ってください:
         1) Render 側で `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` / `DATABASE_HOST` / `DATABASE_PORT` を手動で設定する（Render の DB 作成後に値をコピーして貼る）。
         2) または `dj-database-url` を使って `DATABASE_URL` をパースするよう `settings.py` を変更する（推奨）。

- Frontend に設定するもの:
   - `VITE_BACKEND_URL` = `https://<backend-service-url>`（例: `https://fanart-viewer-backend.onrender.com`）。フロントはこの URL を使って API を呼び出します。フロントはビルド時にこの変数を参照する場合があるため、設定後に再デプロイが必要です。

3) Backend のプラン・ビルド注意点
- このプロジェクトのバックエンドは Playwright とブラウザ（Chrome）を含むため、イメージが大きくビルド・起動に時間とリソースを要します。Render の無料プランではビルド/実行に制限がある可能性があるため、有料プラン（Starter 以上）を選ぶことを推奨します。
- `backend/entrypoint.sh` は起動時にマイグレーションと `collectstatic`、および `import_json_data` を実行します（初回デプロイ時に問題ありません）。
- 本番用途では `runserver` ではなく `gunicorn` を利用します（このリポジトリでは既に `entrypoint.sh` を `gunicorn` 起動に変更済み）。

4) Frontend の注意点
- `frontend/Dockerfile` はマルチステージでビルドし、生成された `dist` を nginx で配信します。フロントは `VITE_BACKEND_URL` を参照するように設定してください。

5) 初回デプロイ手順
- Render のダッシュボードで対象サービスの Deploy を開始（Auto-Deploy を有効にしていれば `main` への push で自動デプロイされます）。
- ビルドログを確認:
   - フロント: `npm ci` → `npm run build` が成功すること。
   - バック: apt / pip / playwright のインストールとブラウザインストールが完了するまで待ちます（長時間かかる場合あり）。

6) デプロイ完了後の確認（ログ・ヘルスチェック）
- バックエンドのログで次を確認:
   - `makemigrations` / `migrate` が走っている
   - `collectstatic` が実行されている
   - `gunicorn` が `0.0.0.0:8000` で起動している
- フロントのログで nginx が起動し、`/` にアクセスできることを確認します。

7) フロントとバックを接続する
- バックエンドの公開 URL（例: `https://fanart-viewer-backend.onrender.com`）を取得して、フロントサービスの環境変数 `VITE_BACKEND_URL` に設定します。変数を更新した場合はフロントを再デプロイしてください。

8) 動作テスト（簡単なコマンド例）
- API 応答確認（Backend が公開されている場合）:
```bash
curl -sS https://<your-backend-url>/api/items/?page_size=5 | jq .
```
- フロントが正しく API を叩いているかは、ブラウザでフロントの URL を開いて操作するか、ブラウザ DevTools の Network タブで API リクエストが通っているか確認してください。

9) ローカルでの事前動作確認（任意）
- フロントの Docker イメージビルド確認:
```bash
docker build -f frontend/Dockerfile -t fanart-frontend-test .
```
- バックエンドの Docker イメージビルド（Playwright のブラウザダウンロードが含まれるため時間がかかります）:
```bash
docker build -f backend/Dockerfile -t fanart-backend-test .
```
- 簡易ローカル検証（Postgres をコンテナで立てる）:
```bash
# 起動
docker run -d --name fv-postgres -e POSTGRES_DB=fanart -e POSTGRES_USER=fanart -e POSTGRES_PASSWORD=password -p 5432:5432 postgres:15

# バックエンド（同一ネットワークで起動するか、--network を使うことを推奨）
docker run --rm -e DATABASE_HOST=host.docker.internal -e DATABASE_PORT=5432 -e POSTGRES_USER=fanart -e POSTGRES_PASSWORD=password -e POSTGRES_DB=fanart -p 8000:8000 fanart-backend-test
```
注: Linux 環境では `host.docker.internal` が使えない場合があります。その場合は Docker ネットワークでコンテナを接続するか、`--network` とコンテナ名を使ってください。

10) トラブルシュート（よくある問題と対処）
- Playwright やブラウザのインストールでビルドが失敗する:
   - ログを確認し、必要な OS パッケージが足りない場合は `backend/Dockerfile` に追加でインストールを追記します。ただしベースが Playwright のイメージなので多くはカバーされています。
- `psycopg2` のビルドエラーが出る:
   - このリポジトリは `psycopg2-binary` を使用しているため通常は問題ありません。もしソースからビルドする必要があるなら `libpq-dev` と `build-essential` 等を Dockerfile に追加してください。
- フロントがバックに接続できない:
   - `VITE_BACKEND_URL` が正しいか、CORS の問題・ALLOWED_HOSTS の設定を確認してください。現在 `backend/settings.py` は `ALLOWED_HOSTS = ['*']` なのでホスト制限は緩い状態です（公開前に絞ることを推奨）。

11) セキュリティ（公開前に必ず実施）
- `DJANGO_DEBUG=0` に設定する。
- `DJANGO_SECRET_KEY` をレンダーの Secret に登録する。
- 必要ならアクセス制限をかける（Render の Basic Auth / Cloudflare Access / IP 制限など）。デプロイ直後は限定公開にして問題点を確認してください。

12) 公開 UI の安全対策（今回のリポジトリに対する注意）
- ユーザが DB の項目やプレビューを破壊的に削除できないよう、`frontend/src/components/PreviewPane.jsx` と `ScrollList.jsx` の該当ボタンはコメントアウトしてあります（公開時の誤操作を防ぐため）。必要な場合は管理者のみが使える別インターフェースを用意してください。

13) 追加の推奨改善（将来的対応）
- Playwright を用いる処理は別ワーカーに分離することを強く推奨します。そうすることでバックエンドのイメージと実行プランを小さく保てます（例: 別の Render Background Worker、別ホスト、あるいはサーバレスジョブ）。
- Render の自動デプロイを使うか、GitHub Actions から Render API を呼んで明示的にデプロイをトリガーするワークフローを作ることを推奨します（API キーが必要）。

14) 便利なコマンド（秘密鍵生成やシークレット設定の例）
- `DJANGO_SECRET_KEY` の生成例:
```bash
python - <<'PY'
import secrets
print(secrets.token_urlsafe(50))
PY
```
- Render に貼る環境変数の例（バックエンドサービス）:
   - `DJANGO_SECRET_KEY`= (上で生成した値)
   - `DJANGO_DEBUG`=0
   - `POSTGRES_USER`=fanart
   - `POSTGRES_PASSWORD`=(Render の DB パスワード)
   - `POSTGRES_DB`=fanart
   - `DATABASE_HOST`=(Render が提供する DB ホスト名)

最後に
---
この手順をそのまま実行すれば Render 上で動作するはずです。私が次にできること:
- Render ダッシュボードに貼る具体的な環境変数テンプレート（コピー＆ペースト用）を作る。
- GitHub Actions で Render デプロイを自動化するワークフローを追加する（Render API Key が必要）。
- Playwright をワーカー分離するための設計案と簡易実装パッチを作る。

どれを先に進めますか？
