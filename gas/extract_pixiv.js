/**
 * Pixivの作品URLから作者名を抽出します。
 * 抽出の優先順位: 1. <title>タグ 2. <meta name="description"> 3. JSONデータ ...
 * * @param {string} pixivUrl 抽出対象のPixiv作品URL
 * @return {string} 抽出された作者名、またはエラーメッセージ
 */
function extractPixivAuthor(pixivUrl) {
  if (!pixivUrl || !pixivUrl.includes("pixiv.net/artworks/")) {
    return "エラー: 無効なPixiv作品URLです。";
  }

  try {
    const response = UrlFetchApp.fetch(pixivUrl, {
      muteHttpExceptions: true,
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/104.0.0.0 Safari/537.36'
      }
    });
    
    if (response.getResponseCode() !== 200) {
      return `エラー: URLアクセス失敗 (HTTPステータス: ${response.getResponseCode()})`;
    }
    
    const html = response.getContentText();
    let authorName = null;
    let match = null;

    // --- 1. <title>タグから抽出を試みる (最優先: ハイブリッド処理) ---
    if (!authorName) {
      const titleFullRegex = /<title[^>]*>(.*)-\s*pixiv<\/title>/i;
      match = html.match(titleFullRegex);

      if (match && match.length > 1) {
        const fullContentBlock = match[1].trim(); 

        const authorExtractRegex = /(.*)\s*の(イラスト|マンガ|漫画)/i;
        const authorBlockMatch = fullContentBlock.match(authorExtractRegex);

        if (authorBlockMatch && authorBlockMatch.length >= 2) {
            let potentialAuthor = authorBlockMatch[1].trim();

            // 🏆 修正箇所: スペースのみを区切り文字として使用 (ハイフンは著者名として保持)
            const parts = potentialAuthor.split(/\s+/).filter(p => p !== ''); 
            
            if (parts.length > 0) {
                 authorName = parts[parts.length - 1].trim(); 
            }
        }
      }
    }

    // --- 2. <meta name="description">タグから抽出を試みる (フォールバック) ---
    if (!authorName) {
      // パターン: ...「[作者名]」さんの(漫画|イラスト|小説)です...
      // 修正: 漫画・イラスト・小説をすべて考慮
      const descRegex = /<meta name="description" content="[^"]*「([^」]+)」さんの(マンガ|漫画|イラスト|小説)です"/i;
      match = html.match(descRegex);

      if (match && match.length > 1) {
        authorName = match[1].trim();
      }
    }
    
    // --- 3. JSON-LD / Data JSON から抽出を試みる (フォールバック) ---
    if (!authorName) {
      // HTML内の script タグに埋め込まれた JSON 構造から作者名（author.name）を抽出
      const jsonLdRegex = /"author":\s*\{[^}]*"name"\s*:\s*"([^"]+)"/i;
      match = html.match(jsonLdRegex);

      if (match && match.length > 1) {
        authorName = match[1].trim();
      }
    }
    
    // --- 4. OGPタイトルタグから抽出を試みる (ハイフン・日本語名対応ロジック) ---
    if (!authorName) {
      // OGPパターン: content="... - [作者名]の(イラスト|マンガ|漫画) [#タグ...] - pixiv"
      // titleタグと同じロジックをOGPタグに適用
      const ogpTitleNewRegex = /<meta property="og:title" content="[^"]*-\s*([^<]*?)の(イラスト|マンガ|漫画)\s*[^"]*-\s*pixiv"/i;
      match = html.match(ogpTitleNewRegex);
      
      if (match && match.length > 1) {
        authorName = match[1].trim();
      }
    }

    // --- 5. OGPタイトルタグから抽出を試みる (従来の | 区切りパターン) ---
    if (!authorName) {
      const ogpTitleOldRegex = /<meta property="og:title" content="[^"]*\|\s*([^#"]+)/i;
      match = html.match(ogpTitleOldRegex);
      
      if (match && match.length > 1) {
        authorName = match[1].trim();
      }
    }
    
    // --- 6. <img>タグのalt属性から抽出を試みる (最終フォールバック) ---
    if (!authorName) {
      const imgAltRegex = /alt="[^"]*-\s*(.*?)のイラスト"/i;
      match = html.match(imgAltRegex);
      if (match && match.length > 1) {
        authorName = match[1].trim();
      }
    }


    if (authorName) {
      // 取得した文字列（作者名）をHTMLエスケープ解除し、UTF-8でデコードして返す
      return Utilities.newBlob(authorName).getDataAsString('UTF-8');
    } else {
      return "エラー: HTML構造が変更され、作者名を抽出できませんでした。";
    }

  } catch (e) {
    return `🔴 エラーが発生しました: ${e.toString()}`;
  }
}

/**
 * スプレッドシートの2番目のシートに対し、
 * F列にあるPixivのリンクから作者名を抽出し、E列に書き込みます。
 */
function extractAuthorsFromPixivLinks() {
  
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  // const allSheets = spreadsheet.getSheets();

  // 🔴 修正点 1: 2ページ目（2番目のシート）の存在チェック
  // if (allSheets.length < 2) {
  //   SpreadsheetApp.getUi().alert("エラー: スプレッドシートに2ページ目（2番目のシート）が存在しません。");
  //   return;
  // }
  
  // 🔴 修正点 2: アクティブなのシートを取得 (インデックスは0から始まるため [1])
  const sheet = spreadsheet.getActiveSheet(); 
  const sheetName = sheet.getName(); // アラート用にシート名を取得
  
  const lastRow = sheet.getLastRow();
  const START_ROW = 2; // データは2行目から開始
  
  // 処理すべき行がない場合は終了
  if (lastRow < START_ROW) {
    SpreadsheetApp.getUi().alert(`シート「${sheetName}」には処理すべきデータ行が見つかりませんでした。`);
    return;
  }

  // E列（作者名）とF列（リンク）の全データを取得
  const range = sheet.getRange(START_ROW, 5, lastRow - START_ROW + 1, 2);
  const values = range.getValues();
  
  const results = [];
  let processedCount = 0;
  
  SpreadsheetApp.getUi().alert(`シート「${sheetName}」のPixiv作者名の抽出を開始します。時間がかかる場合があります。`);
  
  // データを1行ずつ処理
  for (let i = 0; i < values.length; i++) {
    const row = values[i];
    const existingAuthor = row[0]; // E列の値（インデックス0）
    const pixivUrl = row[1];       // F列の値（インデックス1）
    
    // E列に既に値があり、かつF列が空でない場合、処理をスキップ
    if (existingAuthor !== "" && pixivUrl !== "") {
      results.push([existingAuthor]);
      continue;
    }
    
    // F列にURLがない場合
    if (!pixivUrl || String(pixivUrl).trim() === "") {
      results.push([""]);
      continue;
    }
    
    // URLの形式がPixiv作品リンクかチェック
    if (String(pixivUrl).includes("pixiv.net/artworks/")) {
      // 抽出関数を実行
      const author = extractPixivAuthor(String(pixivUrl));
      results.push([author]);
      processedCount++;
    } else {
      // Pixivリンクではない場合
      results.push(["リンク形式エラー"]);
    }

    // 処理の途中でログを記録（任意）
    if ((i + 1) % 50 === 0) {
      Logger.log(`現在 ${i + 1} 行目まで処理完了。`);
    }
  }
  
  // 抽出結果をE列に一括で書き込み
  sheet.getRange(START_ROW, 5, results.length, 1).setValues(results);
  
  SpreadsheetApp.getUi().alert(`✅ シート「${sheetName}」のPixiv作者名の抽出が完了しました。${processedCount} 件の新規/更新データをE列に書き込みました。`);
}