[![技術者倫理 遵守済み](https://img.shields.io/badge/%E6%8A%80%E8%A1%93%E8%80%85%E5%80%AB%E7%90%86-%E9%81%B5%E5%AE%88%E6%B8%88%E3%81%BF-0a0a0a?style=for-the-badge&labelColor=ffffff)](https://技術者倫理.com)
# tutorial

See this [local release guide](RELEASE_LOCAL.md).

# fanart_viewer

This repository contains a Django backend and a React frontend. The local setup is Docker Compose based.

Quick start:

1. Copy env files:

```bash
cp .env.example .env
cp backend/.env.example backend/.env
```

2. Start services:

```bash
docker compose up --build
```

The web service runs migrations on startup.

## Windows スタンドアロンビルド (exe)

アプリを`fanart_viewer.exe`＋付属ファイル一式のフォルダとしてパッケージする方法です（onedirビルド — 単一ファイルではなくフォルダ単位で配布します。理由は後述）。Postgresの代わりにSQLite、dockerなし、pollerも別コンテナではなくアプリ内蔵。ユーザーごとに`%USERPROFILE%\.fanart_viewer`配下に独自のDB・設定が作られ、exe自体にはそれらは同梱されません。ビルドスクリプトは[exe/](exe/)にあります（Windows用`build.ps1`、Linux/WSL用`build.sh` — 後者はLinuxバイナリしか作れず、パッケージング自体の動作確認用です。PyInstallerはクロスコンパイルできないため、実際の.exeはWindows上でしか作れません）。

**なぜonedir（フォルダ配布）なのか：** 単一exe（onefile）は起動のたびに中身を一時フォルダへ自己展開する必要があり、その間ずっと真っ黒な画面になります。onedirならその展開ステップ自体が無くなり、起動が明確に速くなります。なお、どちらの形式でも「解析されにくさ」は変わりません（Pythonバイトコードはどちらの形式でも同じように逆コンパイルできるため、フォルダかexe1個かは難読化の強さとは無関係です）。

### 1. 事前準備

- **Python 3.10または3.11**を[python.org](https://www.python.org/downloads/)からインストール
  （インストール時に「Add python.exe to PATH」にチェック）。インストール後、
  `where.exe python`の結果が`...\Programs\Python\Python3XX\python.exe`を指しているか確認してください。
  `...\AppData\Local\Microsoft\WindowsApps\python.exe`を指している場合はMicrosoft Storeのダミーであり実体のPythonではないため、
  `python --version`や`python -m venv`が何も起こらず失敗します。この場合はWindowsの設定 →
  アプリ → 詳細なアプリ設定 → アプリ実行エイリアスで`python`/`python3`のエイリアスをオフにしてください。
- **Node.js LTS**を[nodejs.org](https://nodejs.org/)からインストール（一覧にあるDocker/nvm等ではなく、
  普通のWindowsインストーラ`.msi`）。フロントエンドのビルドに必要です。`node --version`で確認できます。
- PowerShellが`.ps1`スクリプトの実行そのものを拒否する場合
  （「...スクリプトの実行が無効になっている...」というエラー）、
  `powershell -ExecutionPolicy Bypass -File .\build.ps1`として実行するか、
  そのセッション限りで`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`を先に実行してください。

上記いずれかをインストールした後は、PATHの変更を反映させるため**新しい**PowerShellウィンドウを開き直してください。

### 2. 環境構築＆ビルド（親ディレクトリからの一気通貫スクリプト）

リポジトリ直下（`fanart_viewer\`）にある [`build.ps1`](build.ps1) を実行すると、**`.venv` の作成/有効化 → `git pull` → 依存パッケージ更新 → `frontend` の `npm install` / `npm run build` → `exe\build.ps1`** までをディレクトリ移動なしで一気通貫で実行できます：

```powershell
git clone <このリポジトリ>
cd fanart_viewer
git checkout exe-packaging

# 初回セットアップからビルドまで一括実行（2回目以降の更新もこれ1つでOK）
.\build.ps1
```

### 3. 個別コマンドで実行する場合（参考）

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1

pip install -r backend\requirements.txt
pip install -r exe\requirements.txt
# 領域ラベル付けキューの「自動検出」ボタンが使う人物/頭部検出にはこれも必要
# --no-deps は必須(省略不可) — 依存関係の一部にWindows対応wheelが無いが、
# item.tagger が実際にimportするものはそれらに依存していない
pip install --no-deps dghs-imgutils pyrfc6266

cd frontend
npm install
npm run build
cd ..\exe
.\build.ps1
```

`exe\dist\fanart_viewer\`フォルダが生成されます（`fanart_viewer.exe`はこの中）。配布する際はこのフォルダごと渡してください — `.exe`ファイル単体をコピーしても他の付属ファイルが無いと起動できません。

### 4. アップデート方法（新しいバージョンの反映）

#### A. 配布版（ビルド済み `fanart_viewer` フォルダ / ZIP）を利用している場合

登録済みの画像・DB（`db.sqlite3`）・各種ログイン認証情報・学習済み分類器・ダウンロード済みブラウザ等の永続データはすべて **`%USERPROFILE%\.fanart_viewer`（`C:\Users\<ユーザー名>\.fanart_viewer`）** に分離して保存されており、`fanart_viewer\` フォルダ（`fanart_viewer.exe` と `_internal\`）の中には一切入っていません。

そのため、配布版のアップデートは以下の手順で**フォルダを丸ごと置き換えるだけ**で完了します：

1. 起動中の `fanart_viewer.exe` があれば終了します。
2. 古い `fanart_viewer\` フォルダを削除します（または別名に退避します）。
   ※ 古いフォルダの上に新しいファイルを「上書きコピー」すると、新バージョンで不要になった古いファイルが `_internal\` 内に残って不具合の原因になるため、**古い `fanart_viewer\` フォルダごと削除してから新しいフォルダを配置**してください。
3. 新しいバージョンの `fanart_viewer\` フォルダを展開し、その中の `fanart_viewer.exe` を起動します。
4. `%USERPROFILE%\.fanart_viewer` 内のデータはそのまま自動的に引き継がれ、必要なDBマイグレーションも起動時に自動適用されます。

#### B. ソースコードから自分でビルドしている場合（`git pull` 後）

親ディレクトリ（`fanart_viewer\`）で以下を1行実行するだけで、`.venv` 有効化 → `git pull` → `npm install`（変更時のみ）→ `npm run build` → `exe\build.ps1`（高速差分同期またはフルビルド）まで自動で完了します：

```powershell
.\build.ps1
```

主なオプション（すべて親ディレクトリ `fanart_viewer\` から指定可能）：
- `.\build.ps1` : 通常更新（`.venv` 有効化 → `git pull` → フロントエンドビルド → `exe` 高速差分反映）
- `.\build.ps1 -NoPull` : `git pull` をスキップして現在のローカル変更だけをビルド
- `.\build.ps1 -SkipFrontend` : フロントエンドのビルドをスキップしてバックエンド/exeのみ反映
- `.\build.ps1 -InstallDeps` : `pip install` と `npm install` を強制再実行
- `.\build.ps1 -Full` : PyInstaller によるフルビルドを強制実行
- `.\build.ps1 -Clean` : `exe\build` と `exe\dist` を全消去してクリーンビルド

DBに保存される設定・データ（認証情報、poller設定など）は再ビルド後もそのまま引き継がれます。

### 5. 実行方法

`fanart_viewer\fanart_viewer.exe`をダブルクリックするか、PowerShellから実行してください（最初の数回はこちらを推奨 — 下記参照）。
起動直後は簡単なスプラッシュ画面（起動準備中…）が表示され、その後スピナー付きのウィンドウに切り替わり、
ローカルサーバーが応答するとアプリ画面に切り替わります。ウィンドウを閉じるとアプリは完全に終了します
（裏にプロセスは残りません）。

コンソールウィンドウは表示されません（コンソールアプリではなくウィンドウ型のビルドです）— ウィンドウが表示される前に
何か問題が起きた場合は、`%USERPROFILE%\.fanart_viewer\server.log`でトレースバックを確認してください。

初回セットアップは、アプリ内のヘッダーメニューから全て行えます：
- **Twitter/X 認証情報** — ログイン済みのx.comセッションのCookieから`auth_token`/`ct0`
  （任意で`twid`も）を貼り付けます。同じパネルにバックグラウンドpollerの許可設定
  （デフォルトOFF）と頻度（件数 / 分・時間・日・週ごと）もあります —
  この設定1つでTwitter・Pixiv両方のpollerに反映されます。
- **Pixiv 認証情報** — ログイン済みのpixiv.netセッションのCookieから`PHPSESSID`を貼り付けます
  （ユーザー名/パスワードよりもこちらが確実です。CAPTCHAや2段階認証で失敗することがあるため）。
- **バックアップ** — Google Driveへのバックアップ/復元。Google CloudのOAuthクライアント
  （Console → APIs & Services → Credentials → Create OAuth client ID → Desktop app）を作成し、
  そのClient ID/Secretを「認証する」フォームに貼り付けてください — システムの既定ブラウザで
  Googleの認証画面が開き、得られたトークンが自動的に保存されます（.envの編集は不要です）。

### 6. キャラクター分類器の学習

パッケージ済みのビルドには学習済みの分類器は一切同梱されていません（真っさらな状態）。
自分のDBに溜まった確認済みキャラクターの画像を使って、好きなタイミングで学習させます：

```powershell
cd exe
.\train.ps1
```

進捗は`%USERPROFILE%\.fanart_viewer\training.log`に出力され、`train.ps1`がリアルタイムで
tailして表示します。追加の引数（`--min-images`など、`train_character_classifier`が
受け付けるもの）はそのまま渡せます：

```powershell
.\train.ps1 --min-images 20 --include-multi-character
```

### 7. DBのリセット（開発中の動作確認用）

他人に配布する際、実は何もしなくても相手は綺麗な状態からスタートします —
`%USERPROFILE%\.fanart_viewer`（DB・認証情報・キャッシュ）は各ユーザー自身のホーム
ディレクトリに作られるファイルで、配布するのは`exe\dist\fanart_viewer\`フォルダ
（プログラム本体）だけなので、あなた自身のDBが混ざって渡ることはありません。

開発中、自分の環境で「初回起動と同じまっさらな状態」を試したいときだけ、
DBを消すスクリプトを用意しています（認証情報やpoller設定はそのまま残ります）：

```powershell
cd exe
.\reset_db.ps1
```

## Twitter/X bookmark trigger

The backend exposes `POST /api/items/bookmark_fetch/` for browser-side automation. Use the browser extension in [browser-extension/](browser-extension/) to detect the click and POST the current tweet URL to the local backend. The server resolves the matching Item, fetches the image candidates, and saves them to the DB.

If your backend is not on `http://localhost:8000`, adjust the extension's backend origin in [browser-extension/background.js](browser-extension/background.js).
