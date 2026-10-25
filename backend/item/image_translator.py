"""In-image text translation using Gemini multimodal API.
Detects non-Japanese text (English, Korean, Chinese, etc.) and provides
bounding boxes (0-1000 normalized coordinates) and Japanese translations.
"""
import base64
import json
import logging
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

# Cached model name discovered from successful call
_CACHED_MODEL = None

EXCLUDE_MODEL_SUBSTRINGS = (
    'tts', 'audio', 'embedding', 'embed', 'imagen', 'aqa', 'bison', 'robotics'
)


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


def resolve_available_models(api_key: str) -> list[str]:
    """Fetch active models from ModelService.ListModels supporting `generateContent`,
    strictly excluding audio/TTS/embedding models.
    """
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    req = urllib.request.Request(url, headers={'User-Agent': 'FanartViewer/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        logger.warning("ListModels request failed: %s", e)
        return []

    available = []
    for m in data.get('models', []):
        name = m.get('name', '')  # e.g. "models/gemini-1.5-flash-latest"
        methods = m.get('supportedGenerationMethods', [])
        clean = name.replace('models/', '').lower()

        # Must support generateContent
        if 'generateContent' not in methods:
            continue

        # Exclude non-vision models (TTS, speech, audio, embeddings)
        if any(sub in clean for sub in EXCLUDE_MODEL_SUBSTRINGS):
            continue

        available.append(clean)
    return available


def pick_best_models(api_key: str) -> list[str]:
    """Return an ordered list of candidate models to try.
    Prioritizes proven 1.5-flash series which have full vision input support.
    """
    global _CACHED_MODEL
    # Proven stable multimodal models supporting image input
    preferred_order = [
        'gemini-1.5-flash-latest',
        'gemini-1.5-flash',
        'gemini-1.5-flash-002',
        'gemini-1.5-flash-001',
        'gemini-1.5-flash-8b',
        'gemini-1.5-flash-8b-latest',
        'gemini-1.5-pro-latest',
        'gemini-1.5-pro-002',
        'gemini-1.5-pro',
        'gemini-2.0-flash-exp',
        'gemini-2.0-flash',
    ]

    available = resolve_available_models(api_key)
    candidates = []

    if _CACHED_MODEL and (not available or _CACHED_MODEL in available):
        candidates.append(_CACHED_MODEL)

    if available:
        for pref in preferred_order:
            if pref in available and pref not in candidates:
                candidates.append(pref)
        for m in available:
            if 'flash' in m and m not in candidates:
                candidates.append(m)
        for m in available:
            if 'gemini' in m and m not in candidates:
                candidates.append(m)
    else:
        # Fallback list if ListModels was blocked or failed
        for pref in preferred_order:
            if pref not in candidates:
                candidates.append(pref)

    return candidates


def translate_image_with_gemini(image_bytes: bytes, api_key: str) -> list[dict]:
    """Call Gemini API with the image bytes, returning a list of translation blocks.
    Automatically discovers and falls back across available model versions.
    """
    global _CACHED_MODEL
    if not api_key:
        raise ValueError("Gemini API key is not configured.")

    mime_type = detect_image_mime(image_bytes)
    b64_img = base64.b64encode(image_bytes).decode('ascii')

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

    models_to_try = pick_best_models(api_key)
    logger.info("Attempting Gemini translation with candidate models: %s", models_to_try)

    last_error = None
    resp_body = None

    for model_name in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
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
                _CACHED_MODEL = model_name
                logger.info("Successfully generated content using model: %s", model_name)
                break
        except urllib.error.HTTPError as e:
            body = ''
            msg = ''
            try:
                body = e.read().decode('utf-8', errors='ignore')
                j = json.loads(body)
                msg = j.get('error', {}).get('message', '')
            except Exception:
                pass
            err_msg = msg or f"HTTP {e.code}: {e.reason}"
            last_error = RuntimeError(f"Gemini API エラー ({model_name}): {err_msg}")

            # Check if this model failed due to not existing (404) or image modality not supported (400)
            is_modality_or_not_found = (
                e.code == 404 or
                (e.code == 400 and ('modality' in err_msg.lower() or 'not supported' in err_msg.lower()))
            )
            if is_modality_or_not_found:
                logger.warning("Model %s unsupported for image generateContent (%s), trying next candidate...", model_name, err_msg)
                continue

            # For severe errors (quota exceeded 429, invalid key 403), stop immediately
            raise last_error
        except Exception as e:
            last_error = RuntimeError(f"Gemini API 接続エラー ({model_name}): {str(e)}")
            break

    if resp_body is None:
        if last_error:
            raise last_error
        raise RuntimeError("画像入力に対応した有効な Gemini モデルが見つかりませんでした。")

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
