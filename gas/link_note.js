/**
 * 指定されたNote記事からURLを抽出します。
 * 抽出結果は、スプレッドシートの2番目のシートのF列(F2以降)に書き込みます。
 */
function extractAndWriteUrls() {
  // 抽出対象のNote記事URL
  const NOTE_URL = "";
  
  // 書き込み先の指定
  const TARGET_COLUMN = 6; // F列

  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  const allSheets = spreadsheet.getSheets();

  // 🔴 修正点 1: 2ページ目（2番目のシート）が存在するかチェック
  if (allSheets.length < 2) {
    SpreadsheetApp.getUi().alert("エラー: スプレッドシートに2ページ目（2番目のシート）が存在しません。");
    return;
  }
  
  // 🔴 修正点 2: 2番目のシートを取得 (インデックスは0から始まるため [1])
  const sheet = allSheets[1]; 
  const sheetName = sheet.getName(); // 後のアラート用にシート名を取得

  try {
    // 1. 書き込み開始行を決定 (F列の最後のデータがある次の行)
    // findNextEmptyRow関数には、新しいシートオブジェクト (sheet) を渡します。
    const START_ROW = findNextEmptyRow(sheet, TARGET_COLUMN);

    // 2. HTMLコンテンツの取得
    const response = UrlFetchApp.fetch("https://note.com/ojisan_hobby/n/n72ef6a8f114a?app_launch=false");
    const html = response.getContentText();

    // 3. URLの抽出（正規表現を厳密化）
    const urlRegex = /https?:\/\/[^\s"<>{}\[\]\)\(]+[a-z0-9]/gi; 
    let rawUrls = html.match(urlRegex);

    if (!rawUrls) {
      SpreadsheetApp.getUi().alert("記事から有効なURLが見つかりませんでした。");
      return;
    }

    // 4. 重複の削除とフィルタリング
    let uniqueUrls = [...new Set(rawUrls)];

    const filteredUrls = uniqueUrls.filter(url => {
      // 除外条件: note.com、t.co、pic.twitter.com、ref=、/hashtag/ など
      if (
        url.includes('note.com') ||
        url.includes('t.co/') || 
        url.includes('pic.twitter.com/') || 
        url.includes('ref=') ||
        url.includes('ref_src=') || 
        url.includes('/hashtag/') ||
        url.includes('cloudfront.net') ||
        url.includes('schema.org') ||
        url.includes('google-analytics') ||
        url.includes('*/*') ||
        url.includes('w3.org')
      ) {
        return false; // 除外
      }
      return true;
    });

    if (filteredUrls.length === 0) {
      SpreadsheetApp.getUi().alert("フィルタリングの結果、書き込むべき有効なURLが見つかりませんでした。");
      return;
    }

    // 5. スプレッドシートへの書き込み
    const values = filteredUrls.map(url => [url]);
    const numRows = values.length;

    const range = sheet.getRange(START_ROW, TARGET_COLUMN, numRows, 1);
    range.setValues(values);

    // 🔴 修正点 3: 完了メッセージにシート名を含める
    SpreadsheetApp.getUi().alert(`✅ 抽出した ${numRows} 件のURLをシート「${sheetName}」のF列 F${START_ROW}から書き込みました。`);

  } catch (e) {
    SpreadsheetApp.getUi().alert(`🔴 エラーが発生しました。詳細: ${e.toString()}`);
  }
}

/**
 * 指定されたシートの指定された列において、F2以降の最初の空の行番号を見つけます。
 * @param {GoogleAppsScript.Spreadsheet.Sheet} sheet 対象シート
 * @param {number} column 対象列のインデックス (F列なら 6)
 * @return {number} 書き込みを開始すべき行番号
 */
function findNextEmptyRow(sheet, column) {
  const START_CHECK_ROW = 2; // F2からチェックを開始
  const lastRow = sheet.getLastRow();
  
  // シート全体にデータがない場合、または lastRow が 1 以下の場合、F2から開始
  if (lastRow < START_CHECK_ROW) {
    return START_CHECK_ROW; 
  }

  // F列のF2から最終行までのデータ（二次元配列）を取得
  const data = sheet.getRange(START_CHECK_ROW, column, lastRow - START_CHECK_ROW + 1, 1).getValues();

  // 取得したデータ配列を逆順にチェックし、最後に値が入っている行を探す
  for (let i = data.length - 1; i >= 0; i--) {
    // データが空文字列でない場合、その次の行が書き込み開始行
    if (data[i] && data[i][0] !== "") {
      // (i + 1) はデータ配列内での位置 + 1 (ヘッダー行を除く)
      // + START_CHECK_ROW は実際の行番号に変換
      return START_CHECK_ROW + i + 1;
    }
  }

  // F2以降にデータがない場合、F2から開始
  return START_CHECK_ROW;
}