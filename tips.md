# トラブルシューティングと今後の更新で気を付けること

このプロジェクトで発生した主なエラー、その原因、そして今後同様のトラブルを防ぐための実践的なtipsをまとめます。

## 概要
- 環境: WSL2 上の Linux、Docker / docker-compose を利用
- バックエンド: Django (管理コマンドで JSON → Postgres にインポート)

## 発生した主なエラーと原因（要点）

- **Docker デーモンに接続できない**
  - 原因: WSL2 側で Docker デーモンが起動していない、または Docker Desktop の WSL 統合が有効でない。
  - 対処: Docker Desktop を起動して WSL 統合をONにする。あるいは `dockerd` を手動起動している環境ではそちらを稼働させる。

- **apt パッケージが見つからない (`netcat-postgresql`)**
  - 原因: パッケージ名間違い・存在しないパッケージを指定していた。
  - 対処: 正しいパッケージ名を使用（例: `postgresql-client` と `netcat-openbsd`）。Dockerfile は最小の必要パッケージのみを入れる。

- **SQL エラー: relation "item_item" does not exist（テーブルがない）**
  - 原因: import を実行したタイミングでマイグレーションが作成または適用されていなかった。
  - 対処: コンテナの entrypoint で `makemigrations` -> `migrate` の順に実行する、またはビルド時にマイグレーションファイルを含める。import はマイグレーション後に実行する。

- **同じ JSON を複数回処理してしまう（重複処理）**
  - 原因: 複数の候補ディレクトリを巡回していて、同一ファイルが異なるパスで見つかり重複して処理された。
  - 対処: ファイルを取り扱う際は `os.path.realpath()` 等で正規化して重複除外する、もしくは処理済みファイルの記録（DB テーブルや `.processed` マーカー）を行う。

- **`react-scripts: not found`（フロントエンドが起動しない）**
  - 原因: React アプリが完全に scaffold されていない、または `node_modules`/依存関係が不足している。
  - 対処: `create-react-app` 相当で scaffold するか、`package.json` に必要な依存（`react-scripts` 等）を追加して `npm install` する。コンテナでnpmを実行する前に `package-lock.json` / `yarn.lock` を固定しておく。

- **重複した placeholder の `manage.py` が混在している**
  - 原因: 旧ファイル・サンプルのまま残してしまい、実際の `manage.py` と競合するケース。
  - 対処: 不要な placeholder を削除し、1つの正しい `manage.py` を使う。ファイル名や権限を確認する。

## 今後の更新で気を付けること（実践的チェックリスト）

- **Docker / WSL 環境**
  - Docker Desktop を使う場合は WSL 統合を有効にする。
  - `docker ps` が使えることを確認してから `docker compose up` を実行する。

- **Dockerfile の apt パッケージ**
  - パッケージ名は公式リポジトリで確認する。不要なパッケージは入れない。
  - 安定性のために `apt-get update && apt-get install -y --no-install-recommends <pkgs>` を使う。

- **マイグレーションの順序保証**
  - entrypoint で以下の順に実行することを推奨:
    1. DB の準備待ち（`pg_isready` 等）
    2. `python manage.py makemigrations --noinput`
    3. `python manage.py migrate --noinput`
    4. データ import（`python manage.py import_json_data`）
  - もし import が起動時に失敗する可能性があるなら、import 側で例外を捕まえて再試行（バックオフ）させるか、起動後にキューで import を行う。

- **インポート処理の堅牢化**
  - 同一レコードの上書きは `update_or_create` を使い、外部IDなどの一意キーに基づいて行う。
  - 取り込みファイルは `realpath` で正規化して重複除外する。
  - 各ファイルの処理結果（created/updated/error）を集計し、最後にサマリを出す。
  - 大量データのときはバッチ処理、トランザクションや bulk 操作を検討する（パフォーマンス改善）。

- **フロントエンド**
  - `create-react-app` で scaffold するか、必要な依存を `package.json` に明示して `npm install` する。
  - dev コンテナは `node_modules` をホストとマウントすると問題が起きやすい（プラットフォーム差異）。CI でビルドして成果物だけを配布するパターンを検討する。

- **ファイル配置とマウントの運用**
  - data は一箇所に集約しておく（例: `backend/data`）。複数箇所を探す設計は便利だが重複処理の原因になる。
  - マウントのパス変更やシンボリックリンクの運用に注意して、実際の `os.path.realpath()` を使って正規化する。

- **ログ・監視・テスト**
  - import コマンドには `-v/--verbosity` オプションを活用して詳細ログを出せるようにする。
  - 単体テストで import の小さなケースを作る（既存レコードの更新・新規作成・不正データの扱い）。
  - Docker コンテナにヘルスチェック（`depends_on` と `healthcheck`）を追加すると安定する。

- **CI / デプロイ**
  - Docker イメージのビルドと簡単な `python manage.py check` / `migrate --plan` を CI で実行して、ビルド時の破壊的変更を検出する。
  - 重大な DB スキーマ変更はマイグレーションをチームでレビューする。

## 便利なコマンド集

```bash
# Docker/WSL の確認
docker ps

# Compose ビルド & 起動
(docker compose down)
docker compose up -d --build

# マイグレーション手動実行（コンテナ内）
docker compose run --rm --entrypoint "" web python manage.py makemigrations
docker compose run --rm --entrypoint "" web python manage.py migrate

# インポート手動実行（詳細ログ）
docker compose run --rm --entrypoint "" web python manage.py import_json_data -v 2

# Django シェルで件数確認
docker compose run --rm --entrypoint "" web python manage.py shell -c "from item.models import Item; print(Item.objects.count())"

# webの変更反映
docker compose restart web
docker compose restart frontend

#jsonバックアップ
python3 /home/noko/GitSandbox/fanart_viewer/scripts/convert_dump_to_manosaba.py /home/noko/GitSandbox/fanart_viewer/backend/backup/items-backup-2.json /home/noko/GitSandbox/fanart_viewer/backend/backend/data/manosaba_from_backup.json

#データ削除＋バックアップ＋復帰
docker compose run --rm --entrypoint "" web python manage.py dumpdata item > backend/backup/items-backup-2.json
docker compose run --rm --entrypoint "" web python manage.py makemigrations
docker compose run --rm --entrypoint "" web python manage.py migrate
docker compose run --rm --entrypoint "" web python manage.py shell -c "from item.models import Item; cnt=Item.objects.count(); Item.objects.all().delete(); print('deleted', cnt)"
docker compose run --rm --entrypoint "" web python manage.py import_json_data -v 2
docker compose run --rm --entrypoint "" web python manage.py restore_previews_from_fixture /app//backup/items-backup.json --dry-run
docker compose run --rm --entrypoint "" web python manage.py restore_previews_from_fixture /app/backup/items-backup.json
docker compose run --rm --entrypoint "" web python manage.py shell -c "from item.models import Item; print('total', Item.objects.count()); print('manosaba', Item.objects.filter(source='manosaba').count()); print('mygo', Item.objects.filter(source='mygo').count())"

#画像の取得
##ローカル
curl -X POST http://localhost:8000/api/items/3636/fetch_and_save_preview/ -H "Content-Type: application/json" -d '{}'
curl -X POST http://localhost:8000/api/items/3636/fetch_playwright/ -H "Content-Type: application/json" -d '{}'
docker compose run --rm --entrypoint "" web python manage.py debug_fetch_url 'https://embed.pixiv.net/artwork.php?illust_id=127655657&amp;mdate=1740567729'
##コンテナ内
docker compose run --rm --entrypoint "" web curl -i http://web:8000/api/items/3636/preview/

#メンテナンス
# DB 全体サイズ
docker compose exec db psql -U fanart -d fanart -c "SELECT pg_size_pretty(pg_database_size('fanart')) AS db_size;"
# 例: item_item テーブルをロックしてファイルサイズを縮める（ロック発生）
docker compose exec db psql -U fanart -d fanart -c "VACUUM FULL item_item;"

# 全テーブル実行（本番では業務時間外に）
docker compose exec db psql -U fanart -d fanart -c "VACUUM FULL;"
# 全データ消してシーケンスを1に戻す（注意: データ消えます）
docker compose exec db psql -U fanart -d fanart -c "TRUNCATE item_item RESTART IDENTITY CASCADE;"

# 特定のIDの画像が取得できるかテスト(api.jsonで確認)
curl -s -X POST "http://localhost:8000/api/items/9591/fetch_and_save_preview/"   -H "Content-Type: application/json"   -d '{"preview_only": true, "force_method": "api"}' | jq . > api.json
jq '[.images[] | {index: .index, url: .url, source: .source, content_type: .content_type}]' /home/noko/GitSandbox/fanart_viewer/log/api.json
```

## 最後に（推奨ワークフロー）
- 開発時は `docker compose up --build` の前に `docker ps` を確認し、WSL/Docker の状態を安定させる。
- 重大な変更（Dockerfile、依存、モデルスキーマ）を行うときはローカルで `makemigrations` → `migrate` → `import` の順に手動検証してから entrypoint 自動化を信用する。

---
ファイル: `tips.md` — ここに書かれているチェックリストやコマンドは開発時に簡単に参照できるようにしておくと便利です。問題が再発する場合は該当セクションを追記してください。
