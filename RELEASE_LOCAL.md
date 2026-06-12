# Fanart Viewer — ローカル開発 / ラズパイ本番 セットアップガイド

## 環境の種類

| ファイル | 用途 |
|---|---|
| `docker-compose.yml` | ローカル開発（HMR付きdevサーバー） |
| `docker-compose.prod.yml` | ラズパイ本番（gunicorn + Cloudflare Tunnel） |

---

## ローカル開発環境

### 必要なもの
- Docker (Engine + Compose v2)
- git

### 手順

```bash
# 1. リポジトリをクローン
git clone https://github.com/nokosaaan/fanart_viewer.git
cd fanart_viewer

# 2. .env を作成
cp .env.example .env
# .env を編集して最低限以下を設定:
#   POSTGRES_PASSWORD=任意の強いパスワード
#   DJANGO_SECRET_KEY=ランダムな文字列
#   DJANGO_DEBUG=1

# 3. 起動
docker compose up -d --build

# 4. ログ確認
docker compose logs -f web
```

アクセス先:
- フロントエンド: http://localhost:3000
- バックエンド API: http://localhost:8000/api/
- Django 管理画面: http://localhost:8000/admin/

---

## ラズパイ本番環境（Cloudflare Tunnel）

### 必要なもの
- Docker (Engine + Compose v2)
- Cloudflare アカウント（無料プランでOK）
- ドメイン（Cloudflare 管理下）

### 初回セットアップ

#### 1. Cloudflare Tunnel を作成してトークンを取得

1. [Cloudflare Zero Trust ダッシュボード](https://one.dash.cloudflare.com/) → Networks → Tunnels
2. "Create a tunnel" → Type: Cloudflared → 名前をつける
3. 表示されたトークンをコピー
4. Public Hostname タブで設定:
   - Domain: your-domain.com
   - Service: `http://web:8000`

#### 2. .env を設定

```bash
cp .env.example .env
```

`.env` に以下を設定:

```env
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=<python -c "import secrets; print(secrets.token_hex(32)" の出力>
POSTGRES_PASSWORD=強いパスワード
CLOUDFLARE_TUNNEL_TOKEN=<Cloudflareからコピーしたトークン>
VITE_ADMIN_PATH=<管理者ログイン用のシークレットパス>
ADMIN_PASSWORD=管理者パスワード
VIEWER_PASSWORD=閲覧者パスワード（不要なら空）
```

#### 3. フロントエンドをビルド

```bash
docker compose -f docker-compose.prod.yml run --rm frontend-build
```

#### 4. 起動

```bash
docker compose -f docker-compose.prod.yml up -d
```

#### 5. ラズパイ起動時の自動起動設定（初回のみ）

```bash
# systemd サービスを登録
sudo cp fanart-viewer.service /etc/systemd/system/
# パスをラズパイの実際のパスに合わせて編集
sudo nano /etc/systemd/system/fanart-viewer.service

sudo systemctl daemon-reload
sudo systemctl enable fanart-viewer
sudo systemctl start fanart-viewer
```

### データ更新時の手順

フロントエンドを変更した場合:
```bash
docker compose -f docker-compose.prod.yml run --rm frontend-build
docker compose -f docker-compose.prod.yml restart web
```

バックエンドのみ変更した場合:
```bash
docker compose -f docker-compose.prod.yml up -d --build web
```

全体を更新する場合:
```bash
docker compose -f docker-compose.prod.yml down
docker compose -f docker-compose.prod.yml run --rm frontend-build
docker compose -f docker-compose.prod.yml up -d --build
```

---

## データ管理

### マイグレーションとスーパーユーザー作成（初回）

```bash
# 開発
docker compose exec web python manage.py createsuperuser

# 本番
docker compose -f docker-compose.prod.yml exec web python manage.py createsuperuser
```

### JSON フィクスチャのインポート

```bash
cp /path/to/items-backup.json backend/backup/
docker compose exec web python manage.py import_json_data /app/backup/items-backup.json
```

---

## よく使うコマンド

```bash
# ログ確認
docker compose -f docker-compose.prod.yml logs -f web
docker compose -f docker-compose.prod.yml logs -f cloudflared

# コンテナに入る
docker compose -f docker-compose.prod.yml exec web /bin/bash

# 停止
docker compose -f docker-compose.prod.yml down

# systemd 経由での状態確認
sudo systemctl status fanart-viewer
```

---

Last updated: 2026-06-13
