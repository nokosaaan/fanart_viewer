/**
 * JSON文字列をパースし、スプレッドシートの1ページ目にデータを書き込みます。
 */
function writeJsonToSheet(jsonString) {
  try {
    const data = JSON.parse(jsonString);
    // 現在のシートで追記する
    const sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet(); 
    
    // 1行目のヘッダーを取得
    const headers = sheet.getRange("A1:Z1").getValues()[0]; 
    const START_ROW = 2;
    
    // ヘッダーをキー（プロパティ名）と列インデックスにマッピング
    const headerMap = {};
    headers.forEach((header, index) => {
      if (header && String(header).trim() !== '') {
        headerMap[header.trim()] = index; // ヘッダー名: 列インデックス
      }
    });

    const outputValues = [];

    // JSONのトップレベルキー (ID) を反復処理
    for (const idKey in data) {
      if (!data.hasOwnProperty(idKey)) continue;
      
      const rowData = data[idKey]; // { HEADER_B: value, HEADER_C: [v1, v2] }

      // 新しい行の配列を準備
      const rowArray = new Array(headers.length).fill('');
      
      // A列 (インデックス0) にIDを挿入
      rowArray[0] = idKey; 

      // 行データを処理
      for (const key in rowData) {
        if (!rowData.hasOwnProperty(key)) continue;

        const value = rowData[key];
        const colIndex = headerMap[key.trim()];

        if (colIndex !== undefined) {
          if (Array.isArray(value)) {
            // 配列の場合はカンマ区切り文字列に変換
            rowArray[colIndex] = value.join(', ');
          } else {
            // その他の値はそのまま挿入
            rowArray[colIndex] = value;
          }
        }
      }
      outputValues.push(rowArray);
    }

    if (outputValues.length === 0) {
      return `エラー: JSONファイルに有効なデータが含まれていませんでした。`;
    }

    // 既存のデータをクリア（オプション: IDやヘッダーを除くデータ部分をクリア）
    if (sheet.getLastRow() >= START_ROW) {
        sheet.getRange(START_ROW, 1, sheet.getLastRow(), headers.length).clearContent();
    }
    
    // スプレッドシートに書き込み
    sheet.getRange(START_ROW, 1, outputValues.length, headers.length).setValues(outputValues);

    return `✅ JSONファイルからの書き込みが完了しました。\n${outputValues.length} 件のデータをシート「${sheet.getName()}」に書き込みました。`;

  } catch (e) {
    return `🔴 データ処理エラー: JSONの形式が正しくありません。\n詳細: ${e.message}`;
  }
}

/**
 * HTMLダイアログから受け取ったJSONデータを処理します。
 * @param {string} jsonText 貼り付けられたJSONテキスト
 * @return {string} 処理結果メッセージ
 */
function handleFileUpload(jsonText) {
  if (!jsonText || jsonText.trim() === '') {
    return "エラー: JSONデータが空です。";
  }
  
  // JSONを処理するメイン関数を呼び出す
  return writeJsonToSheet(jsonText);
}

// JSONファイル読み込みダイアログを表示する関数
function showUploadDialog() {
  const htmlOutput = HtmlService
      .createHtmlOutputFromFile('UploadDialog')
      .setWidth(600)
      .setHeight(400);
      
  SpreadsheetApp.getUi().showModalDialog(htmlOutput, 'JSONファイルを読み込み');
}