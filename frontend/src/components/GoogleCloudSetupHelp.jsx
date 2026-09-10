import React from 'react'

const STEP = { marginBottom: 14, fontSize: 13, lineHeight: 1.7, color: '#cbd5e1' }
const NUM = { color: '#93c5fd', fontWeight: 700, marginRight: 6 }

// Provisional, text-only walkthrough for creating the Google Cloud OAuth
// client BackupManager.jsx's authenticate flow needs (client_id/secret) —
// a real screenshot-illustrated version is a separate, later task; this
// exists so the tour's own バックアップ step has something concrete to
// link to right now instead of nothing at all. Steps reflect what
// drive_creds_authenticate_view actually sends (InstalledAppFlow with a
// redirect_uris of ['http://localhost'] under an 'installed' client
// config) — this is exactly why "デスクトップ アプリ" is the required
// application type below, not "ウェブ アプリケーション".
export default function GoogleCloudSetupHelp({ onClose }) {
  return (
    <div className="cgm-panel-backdrop" onClick={onClose}>
      <div className="cgm-panel" style={{ width: 560 }} onClick={e => e.stopPropagation()}>
        <div className="cgm-panel-header">
          <strong>Google Cloud: OAuthクライアントの作り方</strong>
          <button className="cgm-panel-close" onClick={onClose}>✕</button>
        </div>
        <div className="cgm-panel-body">
          <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 16 }}>
            画像付きの手順は今後用意予定です。ひとまずテキストのみの暫定手順です。
          </div>

          <div style={STEP}><span style={NUM}>1.</span>
            <a href="https://console.cloud.google.com/" target="_blank" rel="noopener noreferrer">Google Cloud Console</a> を開き、
            Googleアカウントでログインします。
          </div>
          <div style={STEP}><span style={NUM}>2.</span>
            画面上部のプロジェクト選択から「新しいプロジェクト」を作成します(名前は何でも構いません)。
          </div>
          <div style={STEP}><span style={NUM}>3.</span>
            左側メニューから「APIとサービス」→「ライブラリ」を開き、「Google Drive API」を検索して
            「有効にする」を押します。
          </div>
          <div style={STEP}><span style={NUM}>4.</span>
            「APIとサービス」→「OAuth同意画面」を開き、ユーザータイプは「外部」を選択して作成します。
            アプリ名・サポートメール・デベロッパー連絡先を入力して保存すれば十分です(スコープの追加は不要)。
            「公開ステータス」は「テスト」のままで構いませんが、その場合は同じ画面の「テストユーザー」に
            自分のGoogleアカウントを追加しておいてください。
          </div>
          <div style={STEP}><span style={NUM}>5.</span>
            「APIとサービス」→「認証情報」→「+ 認証情報を作成」→「OAuth クライアント ID」を選びます。
            <strong style={{ color: '#f8fafc' }}>アプリケーションの種類は必ず「デスクトップ アプリ」を選択してください</strong>
            (「ウェブ アプリケーション」ではこのアプリの認証フローが動きません)。名前は任意で「作成」を押します。
          </div>
          <div style={STEP}><span style={NUM}>6.</span>
            作成後に表示される「クライアントID」と「クライアントシークレット」をコピーし、
            このバックアップパネルの入力欄に貼り付けて「認証する」を押してください。
          </div>
        </div>
      </div>
    </div>
  )
}
