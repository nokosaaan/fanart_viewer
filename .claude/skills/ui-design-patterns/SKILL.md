---
name: ui-design-patterns
description: fanart_viewerフロントエンドのUI改善(ホバー/選択状態、ノンバーバルアイコン、進捗表示、ページネーション、控えめなアニメーション)を行う際に使う、具体的なCSS/デザインパターン集。UI/デザイン/アニメーション/アイコン/進捗表示/ページネーションの相談・実装で使用する。
allowed-tools: Read, Edit, Write, Grep, Glob
---

## この skill について

fanart_viewer (frontend/) のUI改善のために、以下6つの外部ソースから抽出した、
実際に使える具体的なCSS/デザインパターンをまとめたもの。単なる参考リンク集ではなく、
このプロジェクトの既存パレット・既存コンポーネント規約に合わせて調整済みの、
そのまま適用できる指針。

出典:
- https://github.com/yui540/css-animations (多段keyframeアニメーションの手法)
- https://github.com/yui540/reanimated-css-animations (React Native/Reanimated — CSSには非直接転用、参考のみ)
- https://web-design-textbook.com/css-advance/ (リストアニメーション — 疑似要素の遅延ズラし手法)
- https://styles.refero.design/ (実プロダクトのデザイントークン抽出集。特にLinearのエントリ)
- https://ui-design-dictionary.pages.dev/ (UIパターン用語辞典 — Pagination/Progress Bar/Micro-interactions等)
- https://kawai-text-animation.pages.dev/ (テキストアニメーション100種のカタログ — 抑制的に使う分だけ採用)

## 既存パレットの実態(重要な前提)

`frontend/src/styles.css` のベースはライトテーマ(`#fff`/`#f3f4f6`/`#6b7280`系)、
一方で新しめのパネル(TwitterCredsManager等)はダークテーマ(`#0f172a`/`#334155`/
`#94a3b8`/`#3b82f6`)をインラインstyleで使っている。**この2系統が混在している**ので、
新規CSSを書くときはどちらの文脈にいるか確認し、そのコンポーネントが既に使っている
系統に合わせること(全面的な統一は別タスクとしてユーザーに確認してから)。

- ライト系 active/hover: `background:#eff6ff; color:#1d4ed8`(既存の`.header-menu-item.active`)
- ダーク系 active/hover: `background:#1e293b`〜`#334155` 相当(次の明度レベルへの反転。
  白反転ではなくトークン1段階分だけ明るくする — refero.design記載のLinear配色が
  「暗い順に4段階の面(#08090a→#0f1011→#161718→#23252a)を積む」設計で、
  ホバー/選択はこの「1段階明るい面」に切り替えるだけ、という考え方が参考になる)
- アクセントは基本1色のみ(このプロジェクトは既に`#3b82f6`が実質の唯一アクセント) —
  新しい色を増やさず、状態の強調は明度/彩度の変化と`#3b82f6`だけで表現する。

## パターン1: ホバー/選択の「色反転」(要求: カーソルを合わせたら選択中と分かる)

`ui-design-dictionary`の"Micro-interactions"カテゴリが指す、まさにこの種の
「hoverで即座に分かるが控えめ」な効果。派手なtransform/keyframesは不要、
背景色と文字色の入れ替え(反転)だけで十分に「選択可能/選択中」が伝わる。

```css
/* 汎用: クリック可能なリスト項目 */
.selectable-row {
  transition: background-color 0.15s ease, color 0.15s ease;
  cursor: pointer;
}
.selectable-row:hover {
  background: var(--hover-bg, #eff6ff);   /* ダーク文脈なら #1e293b */
  color: var(--hover-fg, #1d4ed8);         /* ダーク文脈なら #f1f5f9 のまま可 */
}
.selectable-row.selected {
  background: #3b82f6;
  color: #fff;                              /* 選択中は完全反転でよい(1色アクセント) */
}
```

transitionは`0.15s`前後(refero.design記載のLinear系トークンや
kawai-text-animationの多くのエントリも0.4〜0.8sが「入場」系、ホバーのような
即時フィードバックはそれよりずっと短い0.1〜0.2sが適切)。

### 応用: 疑似要素での2段階マーカー(web-design-textbook由来、任意)

もう一段凝った表現が欲しい箇所(例: メニュー項目やタブの下線)には、
`::before`/`::after`のtransition-delayを逆にする手法が使える
(JSもkeyframesも不要、CSSのtransition-delay切り替えだけ):

```css
.tab-item { position: relative; }
.tab-item::before, .tab-item::after {
  content: ''; position: absolute; background: #3b82f6; transition: all .2s;
}
.tab-item::before { top: calc(50% - 3px); left: 0; width: 6px; height: 6px; transition-delay: .2s; }
.tab-item::after  { top: 100%; left: 6px; width: 0; height: 2px; transition-delay: 0s; }
.tab-item:hover::before { top: 100%; height: 2px; transition-delay: 0s; }
.tab-item:hover::after  { width: 100%; transition-delay: .2s; }
```
小さな点が下線に「変形」→ 下線が伸びる、という2ステップの動きが
transition-delayの入れ替えだけで実現できる。派手すぎない程度の「動きの手応え」。

## パターン2: ノンバーバルアイコン

- 実装済み: `frontend/src/lib/platformIcon.js` + `PlatformBadge.jsx` —
  リンクのドメインからプラットフォーム(Twitter/Pixiv/Poipiku)を判定し、
  対応するSVGバッジ(`/public/icons/{twitter,pixiv,poipiku}.svg`)を出す。
- 新しい機能ボタンにアイコンを付けるときの原則(refero.design記載の
  "Nav Text Button"パターン — 背景なし/枠なし、アイコン+テキストのみ、
  hoverで下線や色変化だけ):
  - アイコンは`currentColor`のstroke系(既存の`copy.svg`/`export-link.svg`と
    同じ流儀)にして、周囲のテキスト色を継承させる。プラットフォームバッジ
    (ブランド色を持つ)だけは例外的に固定色でよい。
  - 必ず`title`属性(またはui-design-dictionaryの"Tooltip"パターンに沿った
    hoverラベル)を付け、アイコンだけでは伝わらない場合の補助にする。
  - サイズは16x16(このプロジェクトの既存アイコンと統一)。

## パターン3: 進捗表示(要求: 「実行中」だけでなく進捗度が分かるように)

- 実装済み: `frontend/src/components/ProgressBar.jsx` — `done`/`total`が
  既にわかっている処理(一括取得など)向けの単純な水平バー+パーセント表示。
  塗りつぶし色はアクセント1色(`#3b82f6`)のみ、`transition: width 0.3s ease`。
- `ui-design-dictionary`の区別: "Progress Bar"(単純な%バー) vs
  "Progress Checklist"(「あと何件」的なチェックリスト表示、Notion/LinkedIn系)。
  件数ベースの処理(取得キューの一括取得など)は後者の言い回し
  (「12/50件処理済み」)をバーと併記すると分かりやすい。
- **進捗データが取れない処理**(DBバックアップ作成、Pixivサルベージの
  ブラケット探索など — 単発の同期リクエストで、バックエンド側に途中経過を
  報告する仕組みがない)は、無理に偽の%を出さず、
  "Loading Spinner"(不確定処理用の回転インジケータ)+ 状況テキストのままでよい。
  正確な進捗表示にはバックエンドの非同期化(ポーリングAPI化)が必要になる
  ため、それ自体は別タスクとして扱うこと。
- 一覧のロード中は、空白のままにせず"Skeleton Screen"
  (読み込み中のプレースホルダー — レイアウトシフトを防ぐ)を検討する。

## パターン4: ページネーション

`ui-design-dictionary`の"Pagination"エントリが挙げる規約
(Google/GitHub Issues系 — 最初の数ページ + 省略記号 + 最終ページ番号)。
挙動:
- 現在ページを中心に前後数ページ分の番号を表示
- 先頭付近(1,2,3...)と末尾(最終ページ番号)は常にクリック可能な番号として出す
- 間が空く場合は省略記号(...)を挟む、またはスクリーンショットの例のように
  単に「先頭数ページ + 最終ページ」を並べるだけでも良い(このプロジェクトの
  データ量なら十分)
- 現在ページは選択中の見た目(パターン1の`.selected`と同じ反転スタイル)
- 既存の「任意ページ番号を直接入力してジャンプ」機能(`PreviewPane.jsx`の
  pane-paginationにある`<input type="number">`)は維持し、番号ボタンと併存させる
  (削除しない — 手動ジャンプの需要は番号ボタンでは代替できない)。

## パターン5: 控えめなアニメーションの取捨選択(kawai-text-animation由来)

**使ってよい(subtle, utility appのトーンに合う)**:
`fadeIn`(opacity 0→1, 0.5〜0.8s)、`slideUp`/`slideDown`/`slideLeft`/`slideRight`
(小さいtranslateY/X + opacity, 0.4〜0.5s)、`scaleIn`(scale 0.95→1 + opacity,
0.4〜0.5s)、`blurIn`(filter:blur→0 + opacity, 0.6〜0.8s — 検索結果の出現などに合う)。

**使わない(kawaii/派手すぎる — このアプリのトーンに合わない)**:
`bounceIn`/`tada`/`jello`/`rubberBand`/`wobble`/`shakeX`/`shakeY`/`swing`/
`flash`/`glitch`系/`colorShift`(虹色サイクル)/`heartbeatBurst`/`elasticScale`。
オーバーシュートするeasing、傾き/回転による歪み、複数色相のサイクルは
すべて「かわいい系」の演出であり、ダーク基調の実用ツールには不向き。

**一度きりの成功通知だけなら許容**: `pulse`/`popIn`のような一瞬だけの強調
(例: 「保存しました」トーストの出現)は、ルーティンなUI状態遷移でなければ
使ってよい。ルーティンなhover/選択状態には絶対に使わない。

## この skill を使うときの進め方

1. 触る対象のコンポーネントが「ライト系」か「ダーク系」かをまず確認する
   (styles.cssのクラス経由か、インラインstyleでダークパレットを使っているか)。
2. 上記パターンのうち該当するものを、そのコンポーネントが既に使っている
   トークンに合わせて適用する(新しい色を増やさない)。
3. アニメーションを足す場合は「パターン5で許可されているものだけ」に絞る。
4. 実装後は実際にフロントエンドをビルドして目視確認する
   (この環境でnpm run buildが権限問題で失敗する場合は、ユーザー側の
   Windows環境でのビルド・目視確認を依頼する)。
