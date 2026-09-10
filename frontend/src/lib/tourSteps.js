// Content for Tour.jsx (see that component for the engine itself) — kept
// separate so the step TEXT can be edited without touching the mechanism.
// Every targetId/groupToggleId here must match a real `data-tour="..."`
// attribute somewhere in the app (HeaderMenu.jsx's MenuEntry, App.jsx's
// own menu item definitions, SearchBar.jsx) — see those files.
//
// Deliberately targets MENU ITEMS almost exclusively, not the deeper
// controls living inside whatever panel a menu item opens (a poller
// toggle, an AI-suggestion card, a bounding-box canvas, ...): those only
// exist in the DOM once their panel is actually open, are the wrong
// shape for a plain click-to-select confirmation dialog (e.g. threading
// "which panel is this" state through this file), and the app rarely
// has example data to show alive during a first-run tour anyway (an
// empty item list has no fetched preview, no suggestion cards, no
// multi-character image to annotate). Describing what's inside a panel
// in the step's own body text, while spotlighting the menu entry that
// opens it, is both far more robust and closer to how a first-time user
// actually experiences the app regardless.
//
// needsHeaderMenu: true on every step whose targetId lives inside the
// header dropdown (see Tour.jsx's own onMenuNeed) — omitted (false) for
// the toggle button itself (shown BEFORE it's clicked, so the dropdown
// should still be closed) and for anything outside the menu entirely
// (the search bar, its filter chips), where a left-open dropdown would
// otherwise sit on top of and hide the very thing being pointed at.

export function buildTourStepsA(){
  return [
    {
      title: 'ようこそ、Fanart Viewerへ',
      body: 'これはあなた専用のイラスト保管庫です。まずは基本的な使い方(つなげる・集める・直す)を順番に案内します。',
    },
    {
      title: 'すべての機能はここから',
      body: 'この☰メニューから、認証情報の設定・取得・編集・バックアップなど全ての機能にアクセスできます。',
      targetId: 'header-menu-toggle',
    },
    {
      title: 'Twitter/Xと連携する',
      body: 'まずはログイン情報を設定しましょう。「ブラウザでログイン」を押すと本物のログイン画面が別ウィンドウで開くので、普段通りログインするだけで設定できます(開発者ツールは不要です)。ここから自動でブックマーク等を取得する設定もできます。',
      groupToggleId: 'menu-group-twitter',
      targetId: 'menu-twitter-creds',
      needsHeaderMenu: true,
    },
    {
      title: 'Pixivと連携する',
      body: 'Twitterと同じく、ブラウザでログインするだけで設定できます。R18作品の取得にも使われます。ブックマークの自動取得もここから設定できます。',
      groupToggleId: 'menu-group-pixiv',
      targetId: 'menu-pixiv-creds',
      needsHeaderMenu: true,
    },
    {
      title: 'Poipikuと連携する(整備中)',
      body: 'Poipikuにも対応していますが、自動取得はまだベータ段階です。うまく動かない場合は手動での取得をお試しください。',
      targetId: 'menu-poipiku-creds',
      needsHeaderMenu: true,
    },
    {
      title: 'リンクから取得',
      body: '投稿のリンクを1件ずつ、またはここ「取得キュー」からまとめて取得できます。取得方法(HTML解析/API/ブラウザ経由)も選べます。',
      targetId: 'menu-fetch-queue',
      needsHeaderMenu: true,
    },
    {
      title: '手元の画像を直接登録',
      body: '元の投稿が既に無くても、パソコンに保存済みの画像ファイルを直接アップロードして登録できます。これは完全に手動での登録専用の機能です。',
      targetId: 'menu-manual-add',
      needsHeaderMenu: true,
    },
    {
      title: 'リンク切れ・削除済みの対策',
      body: 'Pixivの投稿が削除されていても、CDNサルベージや外部検索サイトで画像が見つかることがあります(見つかったらラッキー程度に考えてください)。それでも見つからない場合、手元に画像があればアップロードして当てはめることができます。手元にも無い場合はそのアイテムを削除するのがおすすめですが、リンクだけ記録として残しておくことも選べます。',
    },
    {
      title: 'ここまでで基本は完了です',
      body: 'アイテムが増えてくると、検索・AIによる自動提案・バックアップなど、さらに便利な機能が使えるようになります。10件ほど溜まったら、続きのツアーをご案内します。いつでもこのメニュー内の💡アイコンから見返せます。',
    },
  ]
}

export function buildTourStepsB(){
  return [
    {
      title: '増えてきたら検索',
      body: 'タイトル・キャラ・タグ・作者名で検索できます。',
      targetId: 'search-input',
    },
    {
      title: '絞り込みフィルタ',
      body: '「Preview: MISSING」で未取得のアイテムだけ、「Title: MISSING」でタイトル未設定のアイテムだけを絞り込めます。',
      targetId: 'filter-preview-missing',
    },
    {
      title: 'アイテムを編集する',
      body: 'タイトル・キャラクター・タグ・シチュエーション・作者を編集します。データが増えると、AIがスコア付きの候補(最大3件)を提案するようになります。どの根拠(ハッシュタグ・作家履歴・画像解析・Danbooru照合など)から来た提案かも表示されます。',
      targetId: 'menu-edit-queue',
      needsHeaderMenu: true,
    },
    {
      title: 'キャラクターグループ',
      body: 'どのキャラがどのタイトルに属するかをグループにまとめておくと、AI提案の精度が上がります。新しいタイトル+新しいキャラを同時に登録すると自動でグループが作られます。',
      groupToggleId: 'menu-group-character',
      targetId: 'menu-character-groups',
      needsHeaderMenu: true,
    },
    {
      title: 'Danbooruリンク',
      body: '画像解析(タガー)はDanbooru由来のタグしか認識できないため、キャラ名とDanbooruタグを紐付けておくと認識精度が大きく上がります。カタカナ名でDanbooruでの綴りが分からない場合は、そのまま入力すると別表記(other_names)から候補を探して表示します。',
      groupToggleId: 'menu-group-character',
      targetId: 'menu-character-danbooru-link',
      needsHeaderMenu: true,
    },
    {
      title: '複数キャラの領域ラベル付け',
      body: '1枚に複数のキャラが写っている画像は、どの領域が誰かを手動で矩形指定できます。ここでラベル付けしたデータは学習時の確実な教師データとして使われます。',
      targetId: 'menu-region-queue',
      needsHeaderMenu: true,
    },
    {
      title: '分類器の学習',
      body: 'ここまでのデータを使って、あなた専用のAIモデルを学習させます。最低画像枚数・除外キャラ・複数キャラ画像を含めるか等を指定できます。学習が終わったら、アプリのウィンドウを閉じてfanart_viewer.exeをもう一度起動すると反映されます(このパネルを閉じるだけでは反映されません)。',
      targetId: 'menu-train-classifier',
      needsHeaderMenu: true,
    },
    {
      title: 'Preview Timelineで眺める',
      body: '集めた画像をギャラリーとして閲覧できます。ホイールまたは↑/↓キーで前後のアイテムへ、←/→キーで同じアイテム内の複数ページ送りができます。「画像を新しいタブで開く」からパソコンに保存することもできます。気に入らない画像は削除して「＋」ボタンや取得キューから再取得すれば選び直せます。',
      targetId: 'menu-preview-timeline',
      needsHeaderMenu: true,
    },
    {
      title: 'バックアップ',
      body: 'Google Driveへワンクリックでバックアップでき、進捗もリアルタイムで表示されます。初回のみGoogle CloudでOAuthクライアントの作成が必要です(パネル内に手順を用意しています)。',
      targetId: 'menu-backup',
      needsHeaderMenu: true,
    },
    {
      title: 'これで一通りの案内は終わりです',
      body: 'また見たくなったら、このメニュー内の💡アイコンからいつでも呼び出せます。',
    },
  ]
}
