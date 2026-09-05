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

## AI提案パイプライン: キャラリンク・分類器学習

CharacterDanbooruLink（タガーのDanbooruタグ→DB内の日本語キャラ名を橋渡し）と、独自キャラ分類器（`character_classifier_<backend>.joblib`）を本番に反映する手順。既に一度反映済みの環境でキャラ/タイトルを追加した後の再学習にも使う。

### 1. マイグレーション

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py migrate
```

### 2. キャラ↔Danbooruタグ リンクテーブル

初回のみ、レビュー済みのフィクスチャを読み込む:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py loaddata character_danbooru_link_initial
```

その後（初回・追加キャラが出るたび）、まだリンクを試みていないキャラだけを解決する（既存分は自動スキップ）:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py link_danbooru_characters
```

新規に解決された候補は `CharacterDanbooruLink.debug_info` に根拠（どのタイトルのDanbooru wikiロースターと何点でマッチしたか）が残るので、低スコアのもの（目安: 0.6未満は本番の`_match_tagger_characters`では自動適用されない）は目視確認してから使うこと。

特定のキャラだけ再解決したい場合:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py link_danbooru_characters --force --only キャラ名1,キャラ名2
```

### 3. キャラクター分類器の学習

**推奨構成**（実データ検証済み — 2026-09時点）:
- `--classifier logreg`（デフォルトのままでOK）— ArcFace系（`metric_learning`）は学習画像が120枚/キャラを超えないと優位に立たず、本番のアンサンブル全体で見ると分類器の違いはほぼ無風だった
- `--include-multi-character` は付けない — 実データで全アーキテクチャ ±0.1pt、効果なしと確認済み
- `--exclude` には「複数キャラの束ね」ラベル（例: `牢屋敷メンバー`＝全員集合カットの意図しない誤ラベル）を必ず指定。他にないか `character_image_stats` コマンドで事前確認しておく
  - ただし「髪色などの身体的特徴で複数の別OCを意図的に1クラスにまとめたい」ラベル（例: `white`）は**除外しない**。こちらは意図的なクラスであり、分類器の特徴量（タガーの一般タグ確率ベクトル、white_hair等の身体的特徴タグも含む）でそのまま学習できる。ただし単一キャラのクラスより確信度は下がりやすい点に注意（`train_character_classifier.py`のdocstring参照）

```bash
# ONNX標準バックエンド用（現状の本番デフォルト）— /app/data/tagger/character_classifier_onnx.joblib に自動保存
docker compose -f docker-compose.prod.yml exec web python manage.py train_character_classifier \
  --min-images 15 --exclude 牢屋敷メンバー
```

canaryバックエンド用に切り替える場合（下記の判断とセットで）:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py train_character_classifier \
  --backend canary --min-images 15 --exclude 牢屋敷メンバー
```

⚠️ canaryは1推論あたり約7.7秒（ONNXの約3倍）かかる実測あり。全キャラ・全画像の特徴抽出は環境によっては数時間単位でかかることがあるので、余裕のある時間帯に実行すること。

### バックエンド選択の目安

| | ONNX（デフォルト） | canary |
|---|---|---|
| 1推論あたりの速度 | 約2.5秒 | 約7.7秒 |
| Danbooruリンクの効果 | ほぼ無風（タガー自体の認識キャラ数が少ない） | 大幅改善（実データでcharacter精度 21.1%→69.0%） |

canaryはDanbooruリンクと組み合わせて初めて真価を発揮するが、推論速度が本番機材（ラズパイ等）で実用的かは要確認。

### 4. 動作確認

フロントエンドの「統合型の推論を使う」チェックボックス・モデル選択（標準/canary）で実際に数件試す。両方ともデフォルトOFF（先取り式・ONNX標準）のままなので、切り替える場合は明示的な設定変更が必要。

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

Last updated: 2026-09-06
