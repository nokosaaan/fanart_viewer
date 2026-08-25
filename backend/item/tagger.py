"""WD14-style anime tagger (SmilingWolf's ONNX models) for suggesting
characters/tags/rating from a preview image — runs entirely locally, no
cloud API calls, consistent with the rest of this app's design.

Preprocessing/inference here mirrors the reference implementation at
https://huggingface.co/spaces/SmilingWolf/wd-tagger/blob/main/app.py
(pad-to-square, BICUBIC resize, RGB->BGR, no further normalization) —
verified against the actual model rather than guessed.

The model + tag list are downloaded once from Hugging Face on first use and
cached under DATA_DIR so restarts don't re-download. Default model is the
smallest v3 tagger (~380MB, no copyright/series tags — those aren't in the
public tag list at all) since the Pi deployment target has no GPU; override
via TAGGER_MODEL_REPO for a larger/more accurate model on beefier hardware
(e.g. SmilingWolf/wd-eva02-large-tagger-v3).

Output is always a set of suggestions for a human to review — never
auto-committed to an Item. The reference demo's defaults (0.35 general /
0.85 character) are kept as-is: testing against this exact model showed a
lower character threshold floods the list for large franchises (e.g. a
"6+girls"/"everyone" activation drags in dozens of same-series character
tags at once), so 0.85 isn't just conservatism — it's load-bearing.

Measured ~5s/image on a modern x86 CPU (model already loaded); expect
noticeably slower on the Pi4 deployment target (no GPU, weaker CPU) — this
is an on-demand single-image operation (triggered from the edit form), not
something to run in a tight loop over many items.
"""
import io
import os
import threading

import numpy as np

DEFAULT_MODEL_REPO = 'SmilingWolf/wd-vit-tagger-v3'
MODEL_FILENAME = 'model.onnx'
LABELS_FILENAME = 'selected_tags.csv'

# https://github.com/toriato/stable-diffusion-webui-wd14-tagger — tags that
# look like underscore-separated words but are actually kaomoji and
# shouldn't have their underscores turned into spaces.
_KAOMOJIS = {
    '0_0', '(o)_(o)', '+_+', '+_-', '._.', '<o>_<o>', '<|>_<|>', '=_=',
    '>_<', '3_3', '6_9', '>_o', '@_@', '^_^', 'o_o', 'u_u', 'x_x', '|_|', '||_||',
}

RATING_TO_SITUATION_HINT = {
    'explicit': 'R18',
    'questionable': 'R18',
}

_lock = threading.Lock()
_state = {}  # model_repo -> loaded session/labels, populated lazily


def _data_dir():
    base = os.environ.get('TAGGER_CACHE_DIR') or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'tagger'
    )
    os.makedirs(base, exist_ok=True)
    return base


def _model_dir(repo):
    d = os.path.join(_data_dir(), repo.replace('/', '__'))
    os.makedirs(d, exist_ok=True)
    return d


def _download_if_missing(repo):
    import requests

    d = _model_dir(repo)
    model_path = os.path.join(d, MODEL_FILENAME)
    labels_path = os.path.join(d, LABELS_FILENAME)
    for filename, path in ((MODEL_FILENAME, model_path), (LABELS_FILENAME, labels_path)):
        if os.path.exists(path):
            continue
        url = f'https://huggingface.co/{repo}/resolve/main/{filename}'
        tmp_path = path + '.tmp'
        with requests.get(url, stream=True, timeout=180) as r:
            r.raise_for_status()
            with open(tmp_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)
        os.replace(tmp_path, path)
    return model_path, labels_path


def _load(repo):
    if repo in _state:
        return _state[repo]
    with _lock:
        if repo in _state:  # re-check: another thread may have loaded it while we waited
            return _state[repo]
        import csv
        import onnxruntime as ort

        model_path, labels_path = _download_if_missing(repo)

        tag_names = []
        rating_idx, general_idx, character_idx = [], [], []
        with open(labels_path, newline='', encoding='utf-8') as f:
            for i, row in enumerate(csv.DictReader(f)):
                raw_name = row['name']
                tag_names.append(raw_name if raw_name in _KAOMOJIS else raw_name.replace('_', ' '))
                category = row['category']
                if category == '9':
                    rating_idx.append(i)
                elif category == '0':
                    general_idx.append(i)
                elif category == '4':
                    character_idx.append(i)

        session = ort.InferenceSession(model_path, providers=['CPUExecutionProvider'])
        _, target_size, _, _ = session.get_inputs()[0].shape

        state = {
            'session': session,
            'tag_names': tag_names,
            'rating_idx': rating_idx,
            'general_idx': general_idx,
            'character_idx': character_idx,
            'target_size': target_size,
        }
        _state[repo] = state
        return state


def _prepare_image(image_bytes, target_size):
    from PIL import Image

    image = Image.open(io.BytesIO(image_bytes))
    canvas = Image.new('RGBA', image.size, (255, 255, 255))
    canvas.alpha_composite(image.convert('RGBA'))
    image = canvas.convert('RGB')

    max_dim = max(image.size)
    pad_left = (max_dim - image.size[0]) // 2
    pad_top = (max_dim - image.size[1]) // 2
    padded = Image.new('RGB', (max_dim, max_dim), (255, 255, 255))
    padded.paste(image, (pad_left, pad_top))
    if max_dim != target_size:
        padded = padded.resize((target_size, target_size), Image.BICUBIC)

    arr = np.asarray(padded, dtype=np.float32)
    arr = arr[:, :, ::-1]  # RGB -> BGR: the model was trained this way
    return np.expand_dims(arr, axis=0)


def suggest_tags(image_bytes, general_threshold=0.35, character_threshold=0.85, model_repo=None):
    """Run the tagger on a single image.

    Returns {'characters': [...], 'tags': [...], 'rating': str|None,
    'rating_scores': {...}, 'situation_hint': 'R18'|None} — all suggestions
    for a human to accept/edit/reject, never auto-committed.
    """
    repo = model_repo or os.environ.get('TAGGER_MODEL_REPO') or DEFAULT_MODEL_REPO
    state = _load(repo)
    batch = _prepare_image(image_bytes, state['target_size'])

    session = state['session']
    input_name = session.get_inputs()[0].name
    output_name = session.get_outputs()[0].name
    preds = session.run([output_name], {input_name: batch})[0][0]

    tag_names = state['tag_names']

    ratings = {tag_names[i]: float(preds[i]) for i in state['rating_idx']}
    rating = max(ratings, key=ratings.get) if ratings else None

    general = sorted(
        ((tag_names[i], float(preds[i])) for i in state['general_idx'] if preds[i] > general_threshold),
        key=lambda x: -x[1],
    )
    characters = sorted(
        ((tag_names[i], float(preds[i])) for i in state['character_idx'] if preds[i] > character_threshold),
        key=lambda x: -x[1],
    )

    return {
        'characters': [{'name': n, 'score': round(s, 4)} for n, s in characters],
        'tags': [{'name': n, 'score': round(s, 4)} for n, s in general],
        'rating': rating,
        'rating_scores': {k: round(v, 4) for k, v in ratings.items()},
        'situation_hint': RATING_TO_SITUATION_HINT.get(rating),
    }
