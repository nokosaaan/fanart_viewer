import os
import base64
import json
import logging
import requests

OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
PROVIDER = os.environ.get('AI_TAGGER_PROVIDER', 'openai')


def _call_openai_caption(image_bytes):
    """Call OpenAI Responses API (multimodal) to generate a small JSON with caption/labels.

    This is a proof-of-concept: it sends the image as a data URL and asks the model
    to reply with a JSON object containing `caption`, `labels` (list) and optional
    `characters`, `titles`, `tags` arrays guessed from the image. The exact model
    choice can be tuned via `OPENAI_MODEL` env var; default is `gpt-4o-mini`.
    """
    if not OPENAI_API_KEY:
        logging.info('OPENAI_API_KEY not set; skipping OpenAI call')
        return {}

    model = os.environ.get('OPENAI_MODEL', 'gpt-4o-mini')

    data_url = f"data:image/png;base64,{base64.b64encode(image_bytes).decode('ascii')}"

    # Prompt: ask for strict JSON only
    prompt = (
        "You are an automated image tagger. Given the provided image, return a valid JSON object"
        " with keys: caption (string), labels (list of short strings), characters (list), titles (list), tags (list)."
        " Only return the JSON object and nothing else. Keep lists short (max 8 items)."
    )

    payload = {
        "model": model,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": data_url}
                ]
            }
        ],
        "max_output_tokens": 500
    }

    headers = {
        'Authorization': f'Bearer {OPENAI_API_KEY}',
        'Content-Type': 'application/json'
    }

    try:
        resp = requests.post('https://api.openai.com/v1/responses', headers=headers, data=json.dumps(payload), timeout=30)
        resp.raise_for_status()
        j = resp.json()
        # Extract text output: depends on model response structure; try to find a JSON blob
        # The Responses API may include 'output' sequence with 'content' entries.
        outputs = j.get('output') or j.get('outputs') or j.get('choices')
        text = None
        if isinstance(outputs, list) and outputs:
            # try various shapes
            for o in outputs:
                if isinstance(o, dict):
                    # new Responses API: o.get('content') may be a list
                    cont = o.get('content')
                    if isinstance(cont, list):
                        # find first input_text result
                        for c in cont:
                            if isinstance(c, dict) and c.get('type') == 'output_text':
                                text = c.get('text')
                                break
                        if text:
                            break
                    # fallback to plain text fields
                    if 'text' in o and isinstance(o['text'], str):
                        text = o['text']
                        break
        # fallback: try top-level 'output_text'
        if not text:
            text = j.get('output_text') or j.get('text') or None

        if not text:
            # As final fallback, stringify the whole response
            text = json.dumps(j)

        # The model was instructed to output JSON only. Attempt to parse.
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            # try extracting a JSON substring from text
            import re
            m = re.search(r'\{.*\}', text, re.S)
            if m:
                try:
                    return json.loads(m.group(0))
                except Exception:
                    return {}
            return {}
    except Exception as e:
        logging.exception('OpenAI call failed: %s', e)
        return {}


def tag_image_bytes(image_bytes):
    """Main entry point: returns a dict with possible keys: caption, labels, characters, titles, tags."""
    prov = os.environ.get('AI_TAGGER_PROVIDER', PROVIDER)
    if prov == 'openai':
        return _call_openai_caption(image_bytes)
    # other providers could be added here
    return {}
