/**
 * 貼り付けられたテキストを解析し、アクティブなシートのF列にリンクを追記します。
 * @param {string} linkText 貼り付けられたリンクのテキスト
 * @return {string} 処理結果メッセージ
 */
function handleLinkInput(linkText) {
  if (!linkText || linkText.trim() === '') {
    return "エラー: リンクデータが空です。";
  }
  
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  // ----------------------------------------------------
  // 🌟 修正点 1: アクティブなシートを取得
  const sheet = spreadsheet.getActiveSheet(); 
  // ----------------------------------------------------
  const TARGET_COLUMN = 6; // F列

  // 2ページ目の存在チェックは不要となるため削除

  // ----------------------------------------------------
  // 🌟 修正点 2: 正しいシート名を取得
  const sheetName = sheet.getName();
  // ----------------------------------------------------

  try {
    // 1. テキストを行ごとに分割し、有効なURLのみをフィルタリング
    const lines = linkText.split(/[\r\n]+/); // 改行文字で分割
    const validUrls = lines
      .map(line => line.trim())
      .filter(line => line.length > 0 && (line.startsWith('http://') || line.startsWith('https://')));

    if (validUrls.length === 0) {
      return "エラー: 有効なURLが見つかりませんでした。";
    }

    // 2. 書き込み開始行を決定 (F列の最後のデータがある次の行)
    // 🌟 修正点 3: getLastRow() + 1 で追記開始行を取得
    const START_ROW = sheet.getLastRow() + 1;

    // 3. スプレッドシートへの書き込み用に二次元配列に変換
    const values = validUrls.map(url => [url]);
    const numRows = values.length;

    const range = sheet.getRange(START_ROW, TARGET_COLUMN, numRows, 1);
    range.setValues(values);

    return `✅ リンクの追記が完了しました。\n${numRows} 件のURLをシート「${sheetName}」のF列 F${START_ROW}から書き込みました。`;

  } catch (e) {
    return `🔴 データ処理エラーが発生しました。\n詳細: ${e.toString()}`;
  }
}

// リンク入力ダイアログを表示する関数
function showLinkInputDialog() {
  const htmlOutput = HtmlService
      .createHtmlOutputFromFile('LinkInputDialog') // 新しいHTMLファイル名
      .setWidth(500)
      .setHeight(350);
      
  SpreadsheetApp.getUi().showModalDialog(htmlOutput, 'リンクを貼り付けてアクティブシートのF列に追記');
}