# Fanart Viewer — セットアップ・運用ガイド

## 環境の種類

| ファイル | 用途 |
|---|---|
| `docker-compose.yml` | ローカル開発（HMR付きdevサーバー） |
| `docker-compose.prod.yml` | ラズパイ本番（gunicorn + Cloudflare Tunnel） |

サービス構成:

| サービス | dev (`docker-compose.yml`) | 本番 (`docker-compose.prod.yml`) |
|---|---|---|
| `db` | ○ | ○ |
| `web` | ○ (runserver, ポート8000/3000公開) | ○ (gunicorn, ポート非公開) |
| `poller` | **コメントアウトで無効**（下記参照） | ○ (Twitterブックマーク/いいね自動取得) |
| `frontend` | ○ (devサーバー) | — |
| `frontend-build` | — | ○ (ビルド専用、`--profile build`) |
| `cloudflared` | — | ○ (Quick Tunnel — 下記参照) |

`poller`は`docker-compose.yml`側で現在コメントアウトされており、ローカル開発では動きません。有効化したい場合はファイル内の該当ブロックのコメントを外してください。

---

## セットアップ

### 共通で必要なもの
- Docker (Engine + Compose v2)
- git

### ローカル開発環境

```bash
git clone https://github.com/nokosaaan/fanart_viewer.git
cd fanart_viewer

cp .env.example .env
# .env を編集して最低限以下を設定:
#   POSTGRES_PASSWORD=任意の強いパスワード
#   DJANGO_SECRET_KEY=ランダムな文字列
#   DJANGO_DEBUG=1
```

アクセス先:
- フロントエンド: http://localhost:3000
- バックエンド API: http://localhost:8000/api/
- Django 管理画面: http://localhost:8000/admin/

### ラズパイ本番環境

現状、**Cloudflare Tunnelは「Quick Tunnel（お試し版）」を使用しています**。ドメインもトークンも不要で、`trycloudflare.com`のURLが毎回のコンテナ起動時にランダムに発行されます（固定URLが欲しい場合は末尾の「Named Tunnelへの切り替え」を参照）。

```bash
cp .env.example .env
```

`.env`に最低限設定するもの（**`CLOUDFLARE_TUNNEL_TOKEN`は空のままでOK**）:

```env
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=<python -c "import secrets; print(secrets.token_hex(32))" の出力>
POSTGRES_PASSWORD=強いパスワード
VITE_ADMIN_PATH=<管理者ログイン用のシークレットパス>
ADMIN_PASSWORD=管理者パスワード
VIEWER_PASSWORD=閲覧者パスワード（不要なら空）
```

`poller`（Twitterブックマーク/いいね自動取得）を使う場合、追加で:
- Twitter/Xの認証情報 — 管理画面の「Twitter/X 認証情報」パネルから`auth_token`/`ct0`を設定（`.env`の`TWITTER_AUTH_TOKEN`/`TWITTER_CT0`でも可）
- `NOTIFY_DISCORD_WEBHOOK_URL`（任意） — 認証切れ検知時にDiscordへ通知

Google Driveバックアップを使う場合、追加で`.env`の`GOOGLE_DRIVE_CLIENT_ID`/`GOOGLE_DRIVE_CLIENT_SECRET`/`GOOGLE_DRIVE_REFRESH_TOKEN`を設定（バックアップ節を参照）。

#### フロントエンドをビルド

```bash
docker compose -f docker-compose.prod.yml run --rm frontend-build
```

#### ラズパイ起動時の自動起動設定（初回のみ）

```bash
sudo cp fanart-viewer.service /etc/systemd/system/
sudo nano /etc/systemd/system/fanart-viewer.service  # パスを実際の環境に合わせる

sudo systemctl daemon-reload
sudo systemctl enable fanart-viewer
sudo systemctl start fanart-viewer
```

#### Named Tunnelへの切り替え（固定URLが必要な場合、任意）

1. [Cloudflare Zero Trust ダッシュボード](https://one.dash.cloudflare.com/) → Networks → Tunnels → "Create a tunnel"
2. Public Hostname設定: Domain=your-domain.com、Service=`http://web:8000`
3. `.env`に`CLOUDFLARE_TUNNEL_TOKEN=<コピーしたトークン>`を設定
4. `docker-compose.prod.yml`の`cloudflared`サービスの`command`を`tunnel --no-autoupdate run`に変更し、コメントアウトされている`environment: - TUNNEL_TOKEN=${CLOUDFLARE_TUNNEL_TOKEN}`の行を有効化

---

## 起動

```bash
# 開発
docker compose up -d --build
docker compose logs -f web

# 本番（frontend-buildが済んでいる前提）
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml logs -f web
docker compose -f docker-compose.prod.yml logs -f cloudflared   # Quick TunnelのURLはここに出力される
```

停止:
```bash
docker compose down                              # 開発
docker compose -f docker-compose.prod.yml down   # 本番
```

systemd経由（本番、自動起動設定済みの場合）:
```bash
sudo systemctl status fanart-viewer
sudo systemctl restart fanart-viewer
```

---

## 起動中に使用する可能性のあるコマンド

### 基本操作

```bash
# ログ確認
docker compose -f docker-compose.prod.yml logs -f web
docker compose -f docker-compose.prod.yml logs -f poller
docker compose -f docker-compose.prod.yml logs -f cloudflared

# コンテナに入る
docker compose -f docker-compose.prod.yml exec web /bin/bash

# スーパーユーザー作成（初回）
docker compose exec web python manage.py createsuperuser                              # 開発
docker compose -f docker-compose.prod.yml exec web python manage.py createsuperuser   # 本番
```

### データ投入

```bash
cp /path/to/items-backup.json backend/backup/
docker compose exec web python manage.py import_json_data /app/backup/items-backup.json
```

### 本文(description)の後追い取得

`fetch_and_save_preview`の説明文取得は今後のfetch分にしか効かない。既存アイテムに遡って本文を埋めたい場合:

```bash
# まずdry-runで確認
docker compose -f docker-compose.prod.yml exec web python manage.py backfill_descriptions --dry-run

# 本実行（デフォルト1回30件・リクエスト間1.5秒、レート制限に配慮）
docker compose -f docker-compose.prod.yml exec web python manage.py backfill_descriptions
```

### Twitterブックマーク/いいねの手動ポーリング

通常は`poller`サービスが自動で(6分おきに)実行する。手動で1回だけ試したい場合:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py poll_twitter_updates --once
```

### AI提案パイプライン: キャラリンク・分類器学習

CharacterDanbooruLink（タガーのDanbooruタグ→DB内の日本語キャラ名を橋渡し）と、独自キャラ分類器（`character_classifier_<backend>.joblib`）を反映する手順。既に一度反映済みの環境でキャラ/タイトルを追加した後の再学習にも使う。

#### 1. キャラ↔Danbooruタグ リンクテーブル

初回のみ、レビュー済みのフィクスチャを読み込む:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py loaddata character_danbooru_link_initial
```

その後（初回・追加キャラが出るたび）、まだリンクを試みていないキャラだけを解決する（既存分は自動スキップ）:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py link_danbooru_characters
```

新規に解決された候補は`CharacterDanbooruLink.debug_info`に根拠（どのタイトルのDanbooru wikiロースターと何点でマッチしたか）が残るので、低スコアのもの（目安: 0.6未満は本番の`_match_tagger_characters`では自動適用されない）は目視確認してから使うこと。

特定のキャラだけ再解決したい場合:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py link_danbooru_characters --force --only キャラ名1,キャラ名2
```

#### 2. キャラクター分類器の学習

**推奨構成**（実データ検証済み — 2026-09時点）:
- `--classifier logreg`（デフォルトのままでOK）— ArcFace系（`metric_learning`）は学習画像が120枚/キャラを超えないと優位に立たず、本番のアンサンブル全体で見ると分類器の違いはほぼ無風だった
- `--exclude`には「複数キャラの束ね」ラベル（例: `牢屋敷メンバー`＝全員集合カットの意図しない誤ラベル）を必ず指定。他にないか`character_image_stats`コマンドで事前確認しておく
  - ただし「髪色などの身体的特徴で複数の別OCを意図的に1クラスにまとめたい」ラベル（例: `white`）は**除外しない**。こちらは意図的なクラスであり、分類器の特徴量（タガーの一般タグ確率ベクトル、white_hair等の身体的特徴タグも含む）でそのまま学習できる。ただし単一キャラのクラスより確信度は下がりやすい点に注意（`train_character_classifier.py`のdocstring参照）

```bash
# ONNX標準バックエンド用（現状の本番デフォルト）— /app/data/tagger/character_classifier_onnx.joblib に自動保存
docker compose -f docker-compose.prod.yml exec web python manage.py train_character_classifier \
  --backend onnx --min-images 15 --exclude 牢屋敷メンバー
```

canaryバックエンド用に切り替える場合（下記の判断とセットで）:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py train_character_classifier \
  --backend canary --min-images 15 --exclude 牢屋敷メンバー
```

⚠️ canaryは1推論あたり約7.7秒（ONNXの約3倍）かかる実測あり。全キャラ・全画像の特徴抽出は環境によっては数時間単位でかかることがあるので、余裕のある時間帯に実行すること。

##### バックエンド選択の目安

| | ONNX（デフォルト） | canary |
|---|---|---|
| 1推論あたりの速度 | 約2.5秒 | 約7.7秒 |
| Danbooruリンクの効果 | ほぼ無風（タガー自体の認識キャラ数が少ない） | 大幅改善（実データでcharacter精度 21.1%→69.0%） |

canaryはDanbooruリンクと組み合わせて初めて真価を発揮するが、推論速度が本番機材（ラズパイ等）で実用的かは要確認。

##### 複数キャラ画像を学習に含める場合（--include-multi-character）

デフォルトは単一キャラ画像のみで学習する（上の推奨構成の通り）。付けると次の2種類が両方合流する:
- 領域ラベル付けキュー（`RegionLabelQueueManager`）で人手ラベル済みのregion — 確信度判定なしでそのまま採用
- 未ラベルの複数キャラ画像 — 人物検出のボックス数と確定キャラ数がちょうど一致する画像だけを対象に、単一キャラ画像だけで学習した教師分類器でどのボックスがどのキャラか推定し、確信度(`--bootstrap-confidence`、デフォルト0.7)をクリアしたペアだけ疑似ラベルとして採用する自己学習(self-training)方式

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py train_character_classifier \
  --backend onnx --min-images 15 --exclude 牢屋敷メンバー \
  --include-multi-character --bootstrap-confidence 0.7 \
  --multi-feature-cache /app/data/tagger/character_features_multi_onnx.joblib
```

##### 特徴量キャッシュ（--feature-cache / --use-cache / --multi-feature-cache / --use-multi-cache）

学習コマンドで一番時間がかかるのは分類器のfit自体ではなく、**画像ごとにタガーを1回通す特徴抽出**部分（特にcanaryは1枚約7.7秒＝全画像で数時間かかることもある）。`--exclude`や`--min-images`を変えて何度か試したい場合、毎回特徴抽出からやり直すと非常に無駄なので、キャッシュを使うこと。

キャッシュは指定しなくても**実行するたびに自動保存**される（`/app/data/tagger/character_features_<backend>.joblib`、複数キャラ分は`character_features_multi_<backend>.joblib`）。2回目以降は`--use-cache`/`--use-multi-cache`で明示的に読み込むと、その回は特徴抽出をスキップして数秒で再fitできる。

```bash
# 初回: 特徴抽出＋学習と同時に、抽出結果を自動でキャッシュに保存
docker compose -f docker-compose.prod.yml exec web python manage.py train_character_classifier \
  --backend onnx --min-images 15 --exclude 牢屋敷メンバー

# 2回目以降: --exclude/--min-images/--test-sizeだけ変えて再fit（特徴抽出はスキップ、数秒で終わる）
docker compose -f docker-compose.prod.yml exec web python manage.py train_character_classifier \
  --backend onnx --use-cache /app/data/tagger/character_features_onnx.joblib \
  --exclude 牢屋敷メンバー,別の除外キャラ --min-images 20
```

注意点:
- キャッシュは`--backend`（ONNX/canary）ごとに別ファイルにすること（特徴の次元・意味が違うため使い回せない、backend不一致は自動で拒否される）
- `--use-cache`はキャッシュ作成時の`--min-images`より**低い**値を指定しても、そのキャラの画像自体はキャッシュに含まれていない（キャッシュ作成時点で足切りされている）ため反映されない。キャラの対象範囲を広げたい場合は初回の作成時に低めの`--min-images`を使っておくと、後から絞り込む分には自由に使い回せる
- DBに新しい画像・キャラを追加した後は、キャッシュは古いままなので**再抽出（`--use-cache`を付けずに実行）が必要**。`--use-cache`は「同じデータで学習パラメータだけ変えたい」時専用
- **領域ラベル付けキューの人手ラベル分にはキャッシュが無い** — `--include-multi-character`を付けるたびに毎回タガーへかけ直される。まだ件数が少なければ無視できるが、増えてきたら別途キャッシュ対応が必要

#### 3. 学習後は必ずwebコンテナを再起動する

`train_character_classifier`は`docker compose ... exec web`で**既に動いているwebコンテナの中に別プロセスとして**入り込んで実行される。保存先(`/app/data/tagger/character_classifier_<backend>.joblib`)はbind mount（`./backend/data:/app/data`）なのでファイル自体はホスト側に永続化されるが、実際にリクエストを処理しているgunicornワーカー側は`tagger.py`の`_classifier_state`にモデルをプロセス起動後の初回利用時にメモリキャッシュしているため、学習をやり直してファイルを差し替えても**再起動しない限り古いモデル（または無し）のまま**になる。

```bash
docker compose -f docker-compose.prod.yml down web
docker compose -f docker-compose.prod.yml run --rm frontend-build
docker compose -f docker-compose.prod.yml up -d --build
```

#### 4. 動作確認

フロントエンドの「統合型の推論を使う」チェックボックスは現在デフォルトON（統合型が本番デフォルト）。モデル選択（標準/canary）は引き続き手動選択のまま、実際に数件試す。

---

## バックアップ

Google Driveへのバックアップ（DB全体のpg_dump）機能がある。フロントエンドの「バックアップ」パネルからも実行できるが、**大きいDB（`item_previewimage`テーブルに画像本体が入っている）だとCloudflare Tunnel経由のリクエストが約100秒でタイムアウトする**ことがある（ブラウザには524エラーが出るが、サーバー側では実は継続していることもある）。確実に実行したい場合はmanagement commandをTunnel経由せず直接叩くこと。

```bash
# 作成
docker compose -f docker-compose.prod.yml exec web python manage.py drive_backup create

# 一覧
docker compose -f docker-compose.prod.yml exec web python manage.py drive_backup list

# 復元（--overwriteを付けると既存データを消してから復元）
docker compose -f docker-compose.prod.yml exec web python manage.py drive_backup restore --file-id <id> [--overwrite]
```

事前に`.env`の`GOOGLE_DRIVE_CLIENT_ID`/`GOOGLE_DRIVE_CLIENT_SECRET`/`GOOGLE_DRIVE_REFRESH_TOKEN`の設定が必要（`.env.example`のコメント参照 — `scripts/google_drive_auth.py`で初回のrefresh tokenを取得する）。

### 簡易的なJSONフィクスチャバックアップ（Google Drive未設定の場合の代替）

```bash
docker compose exec web python manage.py dumpdata item > backend/backup/items-backup.json
# 復元
docker compose exec web python manage.py import_json_data /app/backup/items-backup.json
```

より詳しい手順（データ消去・復元込み）は[tips.md](tips.md)を参照。

---

## 更新があった時に利用するコマンド

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

### モデル(models.py)を変更した場合

`entrypoint.sh`/`poller_entrypoint.sh`はコンテナ起動時に`migrate`は自動実行するが、**`makemigrations`(migrationファイルの生成)は自動化されていない**。モデルを変更したら、コンテナ起動前に手動で生成してリポジトリにコミットしておくこと。

```bash
docker compose run --rm --entrypoint "" web python manage.py makemigrations
docker compose run --rm --entrypoint "" web python manage.py migrate
```

### キャラ分類器を学習し直した場合

上記「学習後は必ずwebコンテナを再起動する」を参照 — `docker compose -f docker-compose.prod.yml restart web`が必須。

---

Last updated: 2026-09-08
