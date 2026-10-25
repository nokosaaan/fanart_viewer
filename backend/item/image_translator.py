"""In-image text translation using Gemini 1.5 Flash multimodal API.
Detects non-Japanese text (English, Korean, Chinese, etc.) and provides
bounding boxes (0-1000 normalized coordinates) and Japanese translations.
"""
import base64
import json
import logging
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-1.5-flash"


def detect_image_mime(data: bytes) -> str:
    """Detect image MIME type from initial magic bytes."""
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if data.startswith(b'\xff\xd8'):
        return 'image/jpeg'
    if data.startswith(b'GIF87a') or data.startswith(b'GIF89a'):
        return 'image/gif'
    if data.startswith(b'RIFF') and len(data) >= 12 and data[8:12] == b'WEBP':
        return 'image/webp'
    return 'image/jpeg'


PROMPT = """You are an expert manga, comic, and illustration translator specializing in translating text in images to natural Japanese.
Analyze this image and identify all speech bubbles, dialog, captions, or signs written in languages OTHER than Japanese (specifically English, Korean, Chinese, etc.).

Rules:
1. Ignore pure Japanese text (Hiragana/Katakana/Kanji in Japanese grammar/context) and existing Japanese sound effects.
2. For every non-Japanese text found:
   - Provide the 2D bounding box enclosing the text block in normalized coordinates [ymin, xmin, ymax, xmax] where each value is an integer between 0 and 1000 (0=top/left, 1000=bottom/right).
   - Detect the source language ("en", "ko", "zh", etc.).
   - Extract the original text as faithfully as possible.
   - Translate the text into natural, expressive Japanese fitting the context, tone, and character emotions of manga/anime illustrations.
3. Return a JSON array of objects.

JSON schema:
[
  {
    "box_2d": [ymin, xmin, ymax, xmax],
    "source_lang": "en" | "ko" | "zh" | "other",
    "original_text": "string",
    "translated_text": "自然な日本語訳"
  }
]
If no non-Japanese text is found, return an empty array [].
"""


def translate_image_with_gemini(image_bytes: bytes, api_key: str) -> list[dict]:
    """Call Gemini 1.5 Flash API with the image bytes, returning a list of translation blocks.
    Each item has:
        - box_2d: [ymin, xmin, ymax, xmax] (0..1000)
        - source_lang: str
        - original_text: str
        - translated_text: str
    """
    if not api_key:
        raise ValueError("Gemini API key is not configured.")

    mime_type = detect_image_mime(image_bytes)
    b64_img = base64.b64encode(image_bytes).decode('ascii')

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={api_key}"

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": b64_img
                        }
                    },
                    {
                        "text": PROMPT
                    }
                ]
            }
        ],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.2
        }
    }

    req_data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=req_data,
        headers={
            'Content-Type': 'application/json',
            'User-Agent': 'FanartViewer/1.0'
        },
        method='POST'
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_body = resp.read().decode('utf-8')
    except urllib.error.HTTPError as e:
        err_msg = f"HTTP {e.code}: {e.reason}"
        try:
            body = e.read().decode('utf-8', errors='ignore')
            j = json.loads(body)
            msg = j.get('error', {}).get('message', '')
            if msg:
                err_msg = msg
        except Exception:
            pass
        logger.error("Gemini API request failed: %s", err_msg)
        raise RuntimeError(f"Gemini API エラー: {err_msg}")
    except Exception as e:
        logger.error("Gemini API connection failed: %s", e)
        raise RuntimeError(f"Gemini API 接続エラー: {str(e)}")

    try:
        data = json.loads(resp_body)
        candidates = data.get('candidates', [])
        if not candidates:
            return []
        text_content = candidates[0].get('content', {}).get('parts', [{}])[0].get('text', '[]')
        raw_items = json.loads(text_content)
    except Exception as e:
        logger.warning("Failed to parse Gemini response as JSON: %s (resp: %r)", e, resp_body[:200])
        return []

    if not isinstance(raw_items, list):
        return []

    results = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        box = item.get('box_2d')
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        try:
            ymin, xmin, ymax, xmax = [max(0, min(1000, int(v))) for v in box]
        except (ValueError, TypeError):
            continue

        orig = str(item.get('original_text', '')).strip()
        trans = str(item.get('translated_text', '')).strip()
        if not trans and not orig:
            continue

        results.append({
            'box_2d': [ymin, xmin, ymax, xmax],
            'source_lang': str(item.get('source_lang', 'unknown')),
            'original_text': orig,
            'translated_text': trans or orig,
        })

    return results

