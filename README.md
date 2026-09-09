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

### 2. 環境構築（初回のみ）

```powershell
git clone <このリポジトリ>
cd fanart_viewer
git checkout exe-packaging
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
cd ..
```

### 3. ビルド

```powershell
cd exe
.\build.ps1
```

`exe\dist\fanart_viewer\`フォルダが生成されます（`fanart_viewer.exe`はこの中）。配布する際はこのフォルダごと渡してください — `.exe`ファイル単体をコピーしても他の付属ファイルが無いと起動できません。

### 4. 新しいコードをpullした後の再ビルド

exeはコンパイル済みのスナップショットなので、`git pull`だけでは既に起動中/ビルド済みのものは何も変わりません。
新しいコードを反映するには：`git pull`の後、`frontend\`で`npm run build`（フロントエンドに変更があった場合のみ）、
そして`exe\`で`.\build.ps1`を再実行してください。DBに保存される設定・データ（認証情報、poller設定など）は
再ビルド不要です — 次回起動時、あるいは再起動すら不要で即座に反映されます。

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
