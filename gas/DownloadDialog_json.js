/**
 * スプレッドシートのデータをJSON形式に変換し、Base64エンコードして返します。
 * 1. 1行目をデータのキーとして使用します。
 * 2. 1列目（A列）の値をトップレベルのIDキーとして使用します。
 * 3. TITLE, CHARACTER, TAG列の値（カンマ区切りを想定）を配列に変換します。
 * 4. 結果を整形（インデント・改行）し、Base64でエンコードします。
 */
function getEncodedData() {
  const sheet = SpreadsheetApp.getActiveSheet();
  const dataRange = sheet.getDataRange();
  const allValues = dataRange.getValues();

  // データがない場合は空のオブジェクトのBase64エンコードを返す
  if (allValues.length < 2) {
    return Utilities.base64Encode('{}', Utilities.Charset.UTF_8);
  }

  const headers = allValues[0];
  const dataRows = allValues.slice(1);

  const outputJson = {};
  // 配列に変換する対象のヘッダー名リスト
  const arrayTargetHeaders = ["TITLE", "CHARACTER", "TAG"]; 

  dataRows.forEach(row => {
    // A列の値 (ID) を取得し、トップレベルのキーとして使用
    const idKey = String(row[0]).trim();
    if (idKey === '') {
      return;
    }

    const rowObject = {};
    // B列（インデックス1）から最終列までを処理
    for (let i = 1; i < row.length; i++) {
      const header = String(headers[i]).trim();
      const value = row[i];
      
      if (header !== '') {
        let processedValue = String(value).trim();
        
        if (processedValue !== '') {
            if (arrayTargetHeaders.includes(header)) {
                // 配列化処理: カンマ、全角カンマ、スペースで区切る
                processedValue = processedValue.split(/[,、\s]+/)
                                               .map(item => String(item).trim())
                                               .filter(item => item !== '');
                if (processedValue.length === 0) {
                    continue;
                }
            }
            rowObject[header] = processedValue;
        }
      }
    }
    outputJson[idKey] = rowObject;
  });

  // 改行とインデント（スペース2つ）を付けて整形
  const jsonString = JSON.stringify(outputJson, null, 2);
  
  // Base64でエンコード
  const encoded = Utilities.base64Encode(jsonString, Utilities.Charset.UTF_8);
  return encoded;
}

/**
 * ダウンロード用のHTMLダイアログを表示します。
 */
function showDownloadDialog() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  
  // 🌟 修正点: アクティブなシート（タブ）のオブジェクトを取得し、その名前を取得
  const activeSheet = ss.getActiveSheet();
  const sheetTabName = activeSheet.getName(); 
  // ----------------------------------------------------
  
  // ファイル名にシート名を使用
  // ※ファイル名が日本語の場合、ダウンロード時に文字化けする可能性があるため、
  //   拡張子 (.json) を付けるか、ファイル名処理を調整することを推奨しますが、
  //   今回はご要望の通り sheetTabName をそのまま使用します。
  const fileName = `${sheetTabName}.json`; // ダウンロード時に拡張子を確実に付ける

  const template = HtmlService.createTemplateFromFile('DownloadDialog');
  
  // HTMLテンプレートにファイル名を渡す
  template.fileName = fileName; 
  
  const htmlOutput = template.evaluate()
      .setWidth(300)
      .setHeight(100);
      
  SpreadsheetApp.getUi().showModalDialog(htmlOutput, 'JSONダウンロード');
}

/**
 * スプレッドシートを開いたときにカスタムメニューを追加します。
 */
function onOpen() {
  try {
      SpreadsheetApp.getUi()
          .createMenu('⚙️ JSON出力 / 抽出') // メニュー名を更新
          .addItem('JSONをダウンロード', 'showDownloadDialog')
          .addSeparator() // 区切り線を追加
          .addItem('▶️ Pixiv作者名をE列に抽出', 'extractAuthorsFromPixivLinks') // 新しい項目を追加
          .addSeparator()
          .addItem('⬆️ JSONファイルをスプシに読み込み', 'showUploadDialog') // 新しいメニュー項目
          .addSeparator()
          .addItem('🔗 複数リンクをF列に追記', 'showLinkInputDialog') // 新しいメニュー項目
          .addSeparator()
          .addItem('Twitterユーザー名を抽出してE列に書き込む', 'extractTwitterUsername') // メニュー項目と実行する関数名
          .addToUi();
  } catch(e) {
      Logger.log("カスタムメニューの作成に失敗しました。");
  }
}