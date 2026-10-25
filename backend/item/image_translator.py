"""In-image text translation using Gemini multimodal API.
Detects non-Japanese text (English, Korean, Chinese, etc.) and provides
bounding boxes (0-1000 normalized coordinates) and Japanese translations.

Speed / accuracy notes (why the request is shaped the way it is):
  * The image is downscaled to <= MAX_DIM px and JPEG-compressed before upload.
    Normalized 0-1000 boxes are aspect-ratio relative, so they still map 1:1
    onto the original image.
  * "Thinking" is disabled (thinkingBudget=0 / thinkingLevel=minimal). Recent
    flash models think by default, which alone can add 10-30 s for an OCR task
    that needs no reasoning. If a model rejects the thinking config we retry
    once without it.
  * A responseSchema forces a top-level JSON array. Without it the model
    sometimes wrapped the list in an object ({"texts": [...]}) which the old
    parser silently treated as "no text found".
  * ListModels is cached for the process lifetime so it costs one round-trip
    only on the first translation.
"""
import base64
import io
import json
import logging
import re
import time
import urllib.request
import urllib.error
from PIL import Image

logger = logging.getLogger(__name__)

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
MAX_DIM = 1536            # long edge sent to Gemini (handwriting stays legible)
JPEG_QUALITY = 88
REQUEST_TIMEOUT = 25      # seconds per model attempt
TOTAL_BUDGET = 40         # seconds across all fallback attempts

# Cached model name discovered from successful call
_CACHED_MODEL = None
# Cached ListModels result: (api_key, [model names])
_MODELS_CACHE = None
# thinkingConfig that each model accepted last time (avoids repeating 400 retries)
_THINKING_OK = {}
_UNSET = object()

EXCLUDE_MODEL_SUBSTRINGS = (
    'tts', 'audio', 'embedding', 'embed', 'imagen', 'aqa', 'bison', 'robotics',
    'image-generation', '-image', 'live', 'native-audio', 'computer-use',
    'thinking-exp', 'learnlm', 'gemma', 'veo', 'lyria', 'deep-research',
)

# Fast vision-capable models, best first. Only names that ListModels actually
# reports are used (when ListModels works), so stale names are harmless.
PREFERRED_MODELS = (
    'gemini-flash-latest',
    'gemini-3-flash',
    'gemini-3-flash-preview',
    'gemini-2.5-flash',
    'gemini-flash-lite-latest',
    'gemini-2.5-flash-lite',
    'gemini-2.0-flash',
    'gemini-2.0-flash-001',
    'gemini-1.5-flash-latest',
    'gemini-1.5-flash',
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


def optimize_image_for_api(image_bytes: bytes, max_dim: int = MAX_DIM) -> tuple[bytes, str]:
    """Resize image so its longest dimension <= max_dim and compress to JPEG.
    Maintains aspect ratio so 0-1000 normalized coordinates remain identical.
    Significantly reduces multimodal token count and network payload.
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            img.seek(0)  # first frame of animated GIF/WebP
            w, h = img.size
            if max(w, h) > max_dim:
                scale = max_dim / float(max(w, h))
                new_w = max(1, int(round(w * scale)))
                new_h = max(1, int(round(h * scale)))
                resample = getattr(Image, 'Resampling', Image).LANCZOS
                img = img.resize((new_w, new_h), resample=resample)

            # Convert to RGB if RGBA/palette (fill white background for transparent PNGs)
            if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
                rgba = img.convert('RGBA')
                bg = Image.new('RGB', img.size, (255, 255, 255))
                bg.paste(rgba.convert('RGB'), mask=rgba.split()[3])
                img = bg
            elif img.mode != 'RGB':
                img = img.convert('RGB')

            out = io.BytesIO()
            img.save(out, format='JPEG', quality=JPEG_QUALITY, optimize=True)
            optimized_bytes = out.getvalue()
            logger.info("Optimized image for Gemini: %dx%d (%d bytes -> %d bytes)",
                        img.width, img.height, len(image_bytes), len(optimized_bytes))
            return optimized_bytes, 'image/jpeg'
    except Exception as e:
        logger.warning("Failed to optimize image for API, falling back to original bytes: %s", e)
        return image_bytes, detect_image_mime(image_bytes)


PROMPT = """You are an OCR + translation engine for manga, comics and fan-art illustrations.
Find EVERY piece of readable text in this image that is NOT Japanese and translate it into natural Japanese.

What counts as text (include ALL of these):
- speech bubbles, dialogue, captions, titles, signs, labels, signatures with words, sound effects
- handwritten, brush, stylized, tilted, small or vertical text
- English and other Latin-script languages, Korean (Hangul), and Chinese

Chinese vs Japanese — IMPORTANT:
- Text written only in Chinese characters is CHINESE (not Japanese) when it uses Traditional forms (e.g. 學 樂 麼 們 這 說 對 個 還 給 回 喔 啊 嘛) or Simplified forms (e.g. 们 这 说 对 个 还 给 么), or Chinese grammar/particles (的 了 吗 呢 吧 喔 啦 嗎 你 我 他 是 在 有 想 要).
- Only skip text that is clearly Japanese (contains hiragana/katakana with Japanese grammar).
- Pure Latin-script artist names/handles alone (e.g. "@user123") may be skipped.

For each separate text block (one bubble / one caption = one block):
- box_2d: [ymin, xmin, ymax, xmax], integers 0-1000 normalized to the image size, tightly enclosing the text.
- source_lang: "en", "ko", "zh" or "other".
- original_text: the exact transcription (keep line breaks as \\n).
- translated_text: natural, expressive Japanese matching the character's tone.

Return only the JSON array. Return [] only if there is truly no non-Japanese text."""

RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "box_2d": {"type": "ARRAY", "items": {"type": "INTEGER"}},
            "source_lang": {"type": "STRING"},
            "original_text": {"type": "STRING"},
            "translated_text": {"type": "STRING"},
        },
        "required": ["box_2d", "original_text", "translated_text"],
        "propertyOrdering": ["box_2d", "source_lang", "original_text", "translated_text"],
    },
}


def resolve_available_models(api_key: str) -> list[str]:
    """Fetch active models from ModelService.ListModels supporting `generateContent`,
    strictly excluding audio/TTS/embedding/image-generation models.
    Cached per API key for the process lifetime.
    """
    global _MODELS_CACHE
    if _MODELS_CACHE and _MODELS_CACHE[0] == api_key:
        return _MODELS_CACHE[1]

    url = f"{API_BASE}/models?key={api_key}&pageSize=200"
    req = urllib.request.Request(url, headers={'User-Agent': 'FanartViewer/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        logger.warning("ListModels request failed: %s", e)
        return []

    available = []
    for m in data.get('models', []):
        name = m.get('name', '')  # e.g. "models/gemini-2.5-flash"
        methods = m.get('supportedGenerationMethods', [])
        clean = name.replace('models/', '').lower()
        if 'generateContent' not in methods:
            continue
        if not clean.startswith('gemini'):
            continue
        if any(sub in clean for sub in EXCLUDE_MODEL_SUBSTRINGS):
            continue
        available.append(clean)

    _MODELS_CACHE = (api_key, available)
    logger.info("Gemini models available for image translation: %s", available)
    return available


def _model_rank(name: str) -> tuple:
    """Heuristic ranking for models not in PREFERRED_MODELS: flash > flash-lite > pro,
    non-preview first, newer version first."""
    is_flash = 'flash' in name
    is_lite = 'lite' in name
    is_pro = 'pro' in name
    is_preview = 'preview' in name or 'exp' in name
    ver = 0.0
    mv = re.search(r'gemini-(\d+(?:\.\d+)?)', name)
    if mv:
        try:
            ver = float(mv.group(1))
        except ValueError:
            pass
    tier = 0 if (is_flash and not is_lite) else 1 if is_lite else 2 if is_pro else 3
    return (tier, is_preview, -ver, name)


def pick_best_models(api_key: str) -> list[str]:
    """Return an ordered list of candidate models to try."""
    available = resolve_available_models(api_key)
    candidates = []

    if _CACHED_MODEL and (not available or _CACHED_MODEL in available):
        candidates.append(_CACHED_MODEL)

    if available:
        for pref in PREFERRED_MODELS:
            if pref in available and pref not in candidates:
                candidates.append(pref)
        for m in sorted(available, key=_model_rank):
            if m not in candidates:
                candidates.append(m)
    else:
        # Fallback list if ListModels was blocked or failed
        for pref in PREFERRED_MODELS:
            if pref not in candidates:
                candidates.append(pref)

    return candidates[:6]


def _thinking_attempts_for(model_name: str) -> list:
    """Ordered thinkingConfig variants to try (None = omit the field).
    Aliases like gemini-flash-latest may point at 2.5 (thinkingBudget) or 3.x
    (thinkingLevel), so both are tried before falling back to the default."""
    n = model_name.lower()
    if 'pro' in n and '2.5' in n:
        return [{"thinkingBudget": 128}, None]   # 2.5 Pro cannot fully disable thinking
    if n.startswith('gemini-3') or 'latest' in n:
        return [{"thinkingLevel": "minimal"}, {"thinkingLevel": "low"}, {"thinkingBudget": 0}, None]
    if '2.5' in n:
        return [{"thinkingBudget": 0}, None]
    return [None]


def _build_payload(b64_img: str, mime_type: str, thinking) -> bytes:
    gen_cfg = {
        "response_mime_type": "application/json",
        "response_schema": RESPONSE_SCHEMA,
        "temperature": 0.1,
        "maxOutputTokens": 4096,
    }
    if thinking:
        gen_cfg["thinkingConfig"] = thinking
    payload = {
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": mime_type, "data": b64_img}},
                {"text": PROMPT},
            ]
        }],
        "generationConfig": gen_cfg,
        "safetySettings": [
            {"category": c, "threshold": "BLOCK_NONE"} for c in (
                "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
                "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT",
            )
        ],
    }
    return json.dumps(payload).encode('utf-8')


def _post(model_name: str, api_key: str, body: bytes, timeout: float) -> str:
    url = f"{API_BASE}/models/{model_name}:generateContent?key={api_key}"
    req = urllib.request.Request(
        url, data=body, method='POST',
        headers={'Content-Type': 'application/json', 'User-Agent': 'FanartViewer/1.0'},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode('utf-8')


def _http_error_message(e: urllib.error.HTTPError) -> str:
    try:
        j = json.loads(e.read().decode('utf-8', errors='ignore'))
        msg = j.get('error', {}).get('message', '')
        if msg:
            return msg
    except Exception:
        pass
    return f"HTTP {e.code}: {e.reason}"


def _extract_items(resp_body: str) -> list:
    """Parse generateContent response into a raw list of dicts. Raises on blocked output."""
    data = json.loads(resp_body)
    fb = data.get('promptFeedback', {})
    if fb.get('blockReason'):
        raise RuntimeError(f"Gemini がこの画像の処理を拒否しました (blockReason={fb['blockReason']})")
    candidates = data.get('candidates', [])
    if not candidates:
        return []
    cand = candidates[0]
    parts = cand.get('content', {}).get('parts', []) or []
    text = ''.join(p.get('text', '') for p in parts if not p.get('thought'))
    if not text.strip():
        reason = cand.get('finishReason', '')
        if reason and reason not in ('STOP',):
            raise RuntimeError(f"Gemini の応答が空でした (finishReason={reason})")
        return []

    text = text.strip()
    # Strip ```json fences if any
    fence = re.match(r'^```(?:json)?\s*(.*?)\s*```$', text, re.S)
    if fence:
        text = fence.group(1)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Truncated / noisy output: grab the outermost array
        m = re.search(r'\[.*\]', text, re.S)
        if not m:
            raise
        parsed = json.loads(m.group(0))

    if isinstance(parsed, dict):
        # Wrapped list, e.g. {"texts": [...]} or a single object
        lists = [v for v in parsed.values() if isinstance(v, list)]
        if lists:
            parsed = lists[0]
        elif 'box_2d' in parsed:
            parsed = [parsed]
        else:
            parsed = []
    return parsed if isinstance(parsed, list) else []


def _normalize_items(raw_items: list) -> list[dict]:
    results = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        box = item.get('box_2d') or item.get('box') or item.get('bbox')
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        try:
            ymin, xmin, ymax, xmax = [max(0, min(1000, int(round(float(v))))) for v in box]
        except (ValueError, TypeError):
            continue
        if ymin > ymax:
            ymin, ymax = ymax, ymin
        if xmin > xmax:
            xmin, xmax = xmax, xmin

        orig = str(item.get('original_text', '')).strip()
        trans = str(item.get('translated_text', '')).strip()
        if not trans and not orig:
            continue

        results.append({
            'box_2d': [ymin, xmin, ymax, xmax],
            'source_lang': str(item.get('source_lang', 'unknown') or 'unknown'),
            'original_text': orig,
            'translated_text': trans or orig,
        })
    return results


def translate_image_with_gemini(image_bytes: bytes, api_key: str, return_meta: bool = False):
    """Call Gemini API with the image bytes, returning a list of translation blocks.
    With return_meta=True returns (results, meta) where meta has model / elapsed_ms.
    Automatically discovers and falls back across available model versions.
    """
    global _CACHED_MODEL
    if not api_key:
        raise ValueError("Gemini API key is not configured.")

    t0 = time.monotonic()
    opt_bytes, mime_type = optimize_image_for_api(image_bytes)
    b64_img = base64.b64encode(opt_bytes).decode('ascii')

    models_to_try = pick_best_models(api_key)
    logger.info("Attempting Gemini translation with candidate models: %s", models_to_try)

    last_error = None
    resp_body = None
    used_model = None

    for model_name in models_to_try:
        remaining = TOTAL_BUDGET - (time.monotonic() - t0)
        if remaining < 3:
            break
        known = _THINKING_OK.get(model_name, _UNSET)
        attempts = [known] if known is not _UNSET else _thinking_attempts_for(model_name)
        for th in attempts:
            try:
                t_req = time.monotonic()
                resp_body = _post(model_name, api_key, _build_payload(b64_img, mime_type, th),
                                  timeout=min(REQUEST_TIMEOUT, remaining))
                used_model = model_name
                _THINKING_OK[model_name] = th
                logger.info("Gemini %s responded in %.1fs (thinking=%s)",
                            model_name, time.monotonic() - t_req, th)
                break
            except urllib.error.HTTPError as e:
                err_msg = _http_error_message(e)
                last_error = RuntimeError(f"Gemini API エラー ({model_name}): {err_msg}")
                low = err_msg.lower()
                if e.code == 400 and th and ('thinking' in low or 'budget' in low or 'level' in low):
                    logger.info("Model %s rejected thinkingConfig (%s); retrying without it", model_name, err_msg)
                    continue  # retry same model without thinking config
                if e.code in (404, 400, 500, 503) or (e.code == 429 and 'quota' not in low):
                    logger.warning("Model %s failed (%s), trying next candidate...", model_name, err_msg)
                    break  # next model
                # Invalid key (403) / exhausted quota: stop immediately
                raise last_error
            except Exception as e:
                last_error = RuntimeError(f"Gemini API 接続エラー ({model_name}): {e}")
                logger.warning("Connection/Timeout error with model %s (%s), trying next candidate...", model_name, e)
                break
        if resp_body is not None:
            break

    if resp_body is None:
        if last_error:
            raise last_error
        raise RuntimeError("画像入力に対応した有効な Gemini モデルが見つかりませんでした。")

    _CACHED_MODEL = used_model
    try:
        raw_items = _extract_items(resp_body)
    except RuntimeError:
        raise
    except Exception as e:
        logger.warning("Failed to parse Gemini response as JSON: %s (resp: %r)", e, resp_body[:500])
        raise RuntimeError(f"Gemini の応答を解析できませんでした ({used_model})")

    results = _normalize_items(raw_items)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    if not results:
        logger.info("Gemini (%s) found no text. Raw response head: %r", used_model, resp_body[:500])
    logger.info("Image translation done: %d blocks, model=%s, %d ms", len(results), used_model, elapsed_ms)

    if return_meta:
        return results, {'model': used_model, 'elapsed_ms': elapsed_ms}
    return results
