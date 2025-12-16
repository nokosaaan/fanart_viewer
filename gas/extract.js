/**
 * F列(F2以降)のTwitter URLからユーザー名（@以下の部分）を抽出し、
 * E列(E2以降)に書き込みます。
 * 💡 E列に既に値が入っている場合はスキップします。
 */
function extractTwitterUsername() {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  
  // 最終行を取得 (データが存在する最後の行)
  const lastRow = sheet.getLastRow();
  
  // F2以降にデータがない場合は処理を中断
  if (lastRow < 2) {
    SpreadsheetApp.getUi().alert("F2以降にデータがありません。");
    return;
  }
  
  // 🔴 修正点 1: E列(5列目)とF列(6列目)の2列分を同時に取得
  // getRange(開始行, 開始列, 行数, 列数)
  const dataRange = sheet.getRange(2, 5, lastRow - 1, 2); 
  const values = dataRange.getValues(); // values[i] = [E列の値, F列の値]
  
  // 抽出結果を格納する配列
  const extractedUsernames = [];
  let skipCount = 0;
  let newCount = 0;

  // TwitterのURLからユーザー名を抽出するための正規表現 (ロジックは以前のものを維持)
  const regex = /(twitter|x)\.com\/([^\/]+)\/status/; 

  values.forEach(row => {
    const existingUsername = row[0]; // E列の既存の値
    const url = row[1];             // F列のURL
    let username = "";

    // 🔴 修正点 2: E列に既に値が入っているかチェック
    if (existingUsername && String(existingUsername).trim() !== '') {
      // 既存の値がある場合はそれを結果配列に追加し、スキップする
      extractedUsernames.push([existingUsername]);
      skipCount++;
      return; // この行の処理をスキップ
    }
    
    // F列のURLが空の場合は処理をスキップ
    if (typeof url !== 'string' || url.trim() === '') {
        extractedUsernames.push([""]); // 空のセルを書き込む
        return;
    }
    
    // URLからユーザー名を抽出するロジック
    if (url.includes("twitter.com") || url.includes("x.com")) {
      const match = url.match(regex);
      if (match && match.length > 2) {
          username = match[2]; // ユーザー名
      }
    }
    
    extractedUsernames.push([username]);
    if (username !== "") {
        newCount++;
    }
  });

  
  // E列(E2以降)に結果を書き込み
  // E列は5列目
  const eColumnRange = sheet.getRange(2, 5, extractedUsernames.length, 1);
  eColumnRange.setValues(extractedUsernames);
  
  SpreadsheetApp.getUi().alert(
    `✅ ユーザー名抽出が完了しました。\n` +
    `新規に抽出した件数: ${newCount} 件\n` +
    `スキップした既存データ: ${skipCount} 件`
  );
}