"""WD14-style anime tagger for suggesting characters/tags/rating from a
preview image — runs entirely locally, no cloud API calls, consistent with
the rest of this app's design.

Two backends, chosen per-request (never automatically):

- 'onnx' (default): SmilingWolf's small ONNX models. Preprocessing/inference
  mirrors the reference implementation at
  https://huggingface.co/spaces/SmilingWolf/wd-tagger/blob/main/app.py
  (pad-to-square, BICUBIC resize, RGB->BGR, no further normalization) —
  verified against the actual model rather than guessed. Default model is
  the smallest v3 tagger (~380MB, no copyright/series tags — those aren't
  in the public tag list at all) since the Pi deployment target has no GPU;
  override via TAGGER_MODEL_REPO for a larger/more accurate model on
  beefier hardware (e.g. SmilingWolf/wd-eva02-large-tagger-v3).

- 'timm' (opt-in, see TIMM_MODEL_REPO/HAVE_TIMM): a much larger (~1.3GB),
  far more recently trained model (2026-05-18 Danbooru data) that
  recognizes characters/series the default model's training cutoff simply
  never saw. Needs torch/timm installed — heavy dependencies the Pi4
  deployment target may not want at all, so this is never picked
  automatically; a caller has to ask for backend='timm' explicitly (see
  views.suggest_tags_view's `model` request param). Preprocessing mirrors
  https://github.com/neggles/wdv3-timm (the reference code this model's
  own README links to) — again verified against that script, not guessed.

Both backends' model + tag list are downloaded once from Hugging Face on
first use and cached under DATA_DIR so restarts don't re-download.

Output is always a set of suggestions for a human to review — never
auto-committed to an Item. The reference demo's defaults (0.35 general /
0.85 character) are kept as-is: testing against this exact model showed a
lower character threshold floods the list for large franchises (e.g. a
"6+girls"/"everyone" activation drags in dozens of same-series character
tags at once), so 0.85 isn't just conservatism — it's load-bearing.

Measured ~5s/image on a modern x86 CPU for the default ONNX model (model
already loaded); expect noticeably slower on the Pi4 deployment target (no
GPU, weaker CPU) — this is an on-demand single-image operation (triggered
from the edit form), not something to run in a tight loop over many items.
The 'timm' backend is slower still (larger model, PyTorch CPU inference).
"""
import importlib.util
import io
import logging
import os
import re
import threading

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_MODEL_REPO = 'SmilingWolf/wd-vit-tagger-v3'
MODEL_FILENAME = 'model.onnx'
LABELS_FILENAME = 'selected_tags.csv'

# Optional second backend: a much larger (~1.3GB), far more recently
# trained (2026-05-18 Danbooru data) model that recognizes characters/series
# the default ONNX model's training cutoff simply missed — at the cost of
# requiring torch/timm (heavy dependencies the Pi4 deployment target may not
# want installed at all) and a slower, non-GPU-accelerated forward pass.
# Never used unless a caller explicitly asks for backend='timm' (see
# views.suggest_tags_view's `model` request param) — selecting it is an
# opt-in, not something that happens by default.
TIMM_MODEL_REPO = 'ashen-sensored/wd-eva02-tagger-2026-canary'

# find_spec (not a real import) so merely checking availability doesn't pull
# torch/timm — and the huggingface_hub cache paths they in turn import —
# into memory before _load_timm gets a chance to point HF_HOME at this
# app's own data dir. torch/timm/huggingface_hub are only ever actually
# imported lazily, inside _load_timm/_suggest_tags_timm, once that's set.
HAVE_TIMM = (
    importlib.util.find_spec('torch') is not None
    and importlib.util.find_spec('timm') is not None
)

# https://github.com/toriato/stable-diffusion-webui-wd14-tagger — tags that
# look like underscore-separated words but are actually kaomoji and
# shouldn't have their underscores turned into spaces.
_KAOMOJIS = {
    '0_0', '(o)_(o)', '+_+', '+_-', '._.', '<o>_<o>', '<|>_<|>', '=_=',
    '>_<', '3_3', '6_9', '>_o', '@_@', '^_^', 'o_o', 'u_u', 'x_x', '|_|', '||_||',
}

_COUNT_TAG_RE = re.compile(r'^(\d+)\+?girls$')


def _situation_hint(rating, general_tag_names):
    """Derive a single situation_hint from the rating + booru people-count
    tags. Checked against the FULL general-tag set (before it's capped to
    general_limit for the returned `tags` field) so a low-ranked-but-present
    count tag still counts — these are usually very high-confidence in
    practice, but the cap is about avoiding tag clutter, not about hiding
    structural signals from this logic.

    R18 takes priority when the rating implies it — `situation` is a single
    value in this app's model, and content warning takes precedence over
    composition. Only "1girl"+"solo" -> SOLO and "multiple girls" / an
    explicit 3+ count tag -> MULTIPLE are mapped; 2-person compositions
    (which could plausibly mean CP/pairing) are deliberately left unmapped
    since that's a judgment call, not something the tags settle on their own.
    """
    if rating in RATING_TO_SITUATION_HINT:
        return RATING_TO_SITUATION_HINT[rating]

    names = set(general_tag_names)
    if 'solo' in names and '1girl' in names:
        return 'SOLO'
    if 'multiple girls' in names:
        return 'MULTIPLE'
    for name in names:
        m = _COUNT_TAG_RE.match(name)
        if m and int(m.group(1)) >= 3:
            return 'MULTIPLE'
    return None


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


def _load_onnx(repo):
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


def _prepare_image_onnx(image_bytes, target_size):
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


def _tags_from_predictions(preds, tag_names, rating_idx, general_idx, character_idx,
                            general_threshold, character_threshold, general_limit):
    """Shared by both backends: raw per-tag probabilities -> the same
    {'characters', 'tags', 'tags_full', 'rating', 'rating_scores',
    'situation_hint'} shape, since the two backends only differ in how
    `preds` gets produced (ONNX session vs. torch forward pass)."""
    ratings = {tag_names[i]: float(preds[i]) for i in rating_idx}
    rating = max(ratings, key=ratings.get) if ratings else None

    general_full = sorted(
        ((tag_names[i], float(preds[i])) for i in general_idx if preds[i] > general_threshold),
        key=lambda x: -x[1],
    )
    characters = sorted(
        ((tag_names[i], float(preds[i])) for i in character_idx if preds[i] > character_threshold),
        key=lambda x: -x[1],
    )

    return {
        'characters': [{'name': n, 'score': round(s, 4)} for n, s in characters],
        'tags': [{'name': n, 'score': round(s, 4)} for n, s in general_full[:general_limit]],
        # Uncapped general tags (still threshold-filtered) — kept separate
        # from `tags` above so callers can use the full set for DB
        # similarity matching (more tags = a more specific, reliable match)
        # without also cluttering what's shown to the user as suggested
        # tags in the edit form, which is what general_limit is actually for.
        'tags_full': [n for n, _ in general_full],
        'rating': rating,
        'rating_scores': {k: round(v, 4) for k, v in ratings.items()},
        'situation_hint': _situation_hint(rating, (n for n, _ in general_full)),
    }


def _raw_predict_onnx(image_bytes, model_repo):
    repo = model_repo or os.environ.get('TAGGER_MODEL_REPO') or DEFAULT_MODEL_REPO
    state = _load_onnx(repo)
    batch = _prepare_image_onnx(image_bytes, state['target_size'])

    session = state['session']
    input_name = session.get_inputs()[0].name
    output_name = session.get_outputs()[0].name
    preds = session.run([output_name], {input_name: batch})[0][0]
    return preds, state['tag_names'], state['rating_idx'], state['general_idx'], state['character_idx']


def _load_timm(repo):
    if repo in _state:
        return _state[repo]
    with _lock:
        if repo in _state:
            return _state[repo]
        if not HAVE_TIMM:
            raise RuntimeError(
                "torch/timm are not installed in this environment — this model needs the "
                "optional heavier dependencies (see requirements.txt). Pick the default model instead, "
                "or install torch/timm/safetensors and restart."
            )

        import csv

        # Route this model's (~1.3GB) Hugging Face Hub download/cache
        # through this app's own persistent data dir — same as the ONNX
        # models — instead of timm/huggingface_hub's own default
        # (~/.cache/huggingface), which may not survive a container restart
        # on the Pi deployment target. Must happen before timm/huggingface_hub
        # are actually imported (both compute their cache paths at import
        # time), which is exactly why HAVE_TIMM above only does a spec check.
        os.environ.setdefault('HF_HOME', os.path.join(_data_dir(), 'hf_cache'))

        import timm
        from huggingface_hub import hf_hub_download
        from timm.data import create_transform, resolve_data_config

        model = timm.create_model(f"hf-hub:{repo}", pretrained=True).eval()

        labels_path = hf_hub_download(repo_id=repo, filename=LABELS_FILENAME)
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

        transform = create_transform(**resolve_data_config(model.pretrained_cfg, model=model))

        state = {
            'model': model,
            'transform': transform,
            'tag_names': tag_names,
            'rating_idx': rating_idx,
            'general_idx': general_idx,
            'character_idx': character_idx,
        }
        _state[repo] = state
        return state


def _prepare_image_timm(image_bytes, transform):
    """Mirrors https://github.com/neggles/wdv3-timm (the reference
    inference code this model's own README links to) — verified against
    that script rather than guessed: pad-to-square with a white background,
    then the model's own timm transform (bicubic resize to 448x448,
    normalize to [-1, 1] per pretrained_cfg's mean/std=0.5), then an
    explicit RGB->BGR channel swap AFTER normalization, matching the
    reference exactly.
    """
    from PIL import Image

    image = Image.open(io.BytesIO(image_bytes))
    canvas = Image.new('RGBA', image.size, (255, 255, 255))
    canvas.alpha_composite(image.convert('RGBA'))
    image = canvas.convert('RGB')

    max_dim = max(image.size)
    padded = Image.new('RGB', (max_dim, max_dim), (255, 255, 255))
    padded.paste(image, ((max_dim - image.size[0]) // 2, (max_dim - image.size[1]) // 2))

    tensor = transform(padded).unsqueeze(0)
    tensor = tensor[:, [2, 1, 0]]  # RGB -> BGR, after normalization (per reference script)
    return tensor


def _raw_predict_timm(image_bytes, model_repo):
    repo = model_repo or TIMM_MODEL_REPO
    state = _load_timm(repo)  # sets HF_HOME and imports torch/timm as a side effect, before this next import
    import torch
    tensor = _prepare_image_timm(image_bytes, state['transform'])

    with torch.inference_mode():
        logits = state['model'](tensor)
        preds = torch.sigmoid(logits).squeeze(0).numpy()
    return preds, state['tag_names'], state['rating_idx'], state['general_idx'], state['character_idx']


def _raw_predict(image_bytes, model_repo, backend):
    """The forward pass alone, before any threshold is applied — split out
    from _tags_from_predictions so a threshold sweep (see
    suggest_tags_multi_threshold) can run inference ONCE per image/crop and
    then cheaply re-filter the same per-tag probabilities at many different
    character_threshold values, instead of re-running the model per
    threshold for no reason (the forward pass doesn't depend on the
    threshold at all)."""
    if backend == 'timm':
        return _raw_predict_timm(image_bytes, model_repo)
    return _raw_predict_onnx(image_bytes, model_repo)


def _suggest_tags_single_pass(image_bytes, general_threshold, character_threshold, general_limit, model_repo, backend):
    preds, tag_names, rating_idx, general_idx, character_idx = _raw_predict(image_bytes, model_repo, backend)
    return _tags_from_predictions(
        preds, tag_names, rating_idx, general_idx, character_idx,
        general_threshold, character_threshold, general_limit,
    )


# Anime-art person detector (ONNX, ~11-25M params depending on level — same
# lightweight-inference philosophy as the tagger itself, not the heavier
# 'timm' backend) used to localize characters before tagging. A WD14-style
# tagger sees a whole image as one blended feature vector, so a multi-
# character image can have one character's feature (e.g. eye color) get
# attributed to another, surfacing a character that isn't actually in the
# picture at all. Splitting the image into per-person regions first and
# tagging each crop independently means each tagger call only ever sees one
# character, which can't cross-contaminate. Verified live against a real
# 2-character Danbooru image (Hakurei Reimu + Kirisame Marisa) — produced
# exactly 2 correctly-separated, non-overlapping boxes.
_PERSON_DETECT_CONF_THRESHOLD = 0.3
_MAX_PERSON_CROPS = 4  # bounds worst-case cost on a crowd scene


def _detect_person_boxes(image_bytes):
    """[(x1, y1, x2, y2), ...] in the original image's pixel coordinates,
    most-confident first. Empty list (never raises to the caller — see
    suggest_tags) if detection itself fails for any reason, so a tagger
    request never fails outright over this being an additional, optional
    refinement step.
    """
    from imgutils.detect import detect_person
    from PIL import Image

    image = Image.open(io.BytesIO(image_bytes)).convert('RGB')
    results = detect_person(image, conf_threshold=_PERSON_DETECT_CONF_THRESHOLD)
    ordered = sorted(results, key=lambda r: -r[2])
    return [tuple(int(v) for v in box) for box, _label, _score in ordered]


def _crop_with_padding(image_bytes, box, padding_ratio=0.15):
    """Crops to `box` plus a margin (person-detection boxes can clip hair
    ornaments, hands, etc. right at the edge) and re-encodes as PNG bytes —
    kept as bytes (not a PIL Image) so this feeds straight back into the
    same _suggest_tags_onnx/_suggest_tags_timm entry points a whole image
    would, no separate code path needed for the cropped case.
    """
    from PIL import Image

    image = Image.open(io.BytesIO(image_bytes)).convert('RGB')
    x1, y1, x2, y2 = box
    pad_x, pad_y = int((x2 - x1) * padding_ratio), int((y2 - y1) * padding_ratio)
    x1, y1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
    x2, y2 = min(image.width, x2 + pad_x), min(image.height, y2 + pad_y)

    buf = io.BytesIO()
    image.crop((x1, y1, x2, y2)).save(buf, format='PNG')
    return buf.getvalue()


def suggest_tags(image_bytes, general_threshold=0.35, character_threshold=0.85, general_limit=15,
                  model_repo=None, backend='onnx'):
    """Run the tagger on a single image.

    Returns {'characters': [...], 'tags': [...], 'rating': str|None,
    'rating_scores': {...}, 'situation_hint': 'R18'|None} — all suggestions
    for a human to accept/edit/reject, never auto-committed.

    `general_limit` caps the general (freeform) tag suggestions to the
    top-N by confidence — unlike characters, where the model itself is
    already precision-tuned via a high threshold, general tags can easily
    return dozens of low-value hits (pose, background, clothing details)
    that would clutter the item's tags field more than help it.

    `backend='onnx'` (default) is the small, fast, fully-local model this
    app has always used. `backend='timm'` opts into TIMM_MODEL_REPO — a
    much larger, more recently-trained model that needs torch/timm
    installed (see HAVE_TIMM) and a slower CPU forward pass, but recognizes
    characters/series the default model's training cutoff missed entirely.
    Callers choose per-request (see views.suggest_tags_view's `model` param)
    — nothing here decides that on its own.

    If a person detector finds 2+ people in the image, `characters` is
    re-derived from tagging each person's cropped region individually
    instead of the whole image — see _detect_person_boxes. `tags`/`rating`/
    `situation_hint` always come from the whole image regardless (they
    aren't character-specific, and re-deriving them per-crop would just
    duplicate background/composition tags per detected person for no
    benefit). Single-subject images take exactly the same path as before
    (one detector call that finds 0-1 boxes, no extra tagger passes).
    """
    result = _suggest_tags_single_pass(image_bytes, general_threshold, character_threshold, general_limit, model_repo, backend)

    try:
        boxes = _detect_person_boxes(image_bytes)
    except Exception:
        logger.exception('Person detection failed — falling back to whole-image character tagging')
        boxes = []

    if len(boxes) >= 2:
        merged = {}
        for box in boxes[:_MAX_PERSON_CROPS]:
            try:
                crop_bytes = _crop_with_padding(image_bytes, box)
                crop_result = _suggest_tags_single_pass(
                    crop_bytes, general_threshold, character_threshold, general_limit, model_repo, backend,
                )
            except Exception:
                logger.exception('Per-crop tagging failed for box %s', box)
                continue
            for c in crop_result['characters']:
                existing = merged.get(c['name'])
                if existing is None or c['score'] > existing['score']:
                    merged[c['name']] = c
        if merged:
            # Crop-derived characters supersede (not merge with) the
            # whole-image pass's — that whole-image list is exactly the
            # cross-contamination-prone signal this is meant to replace.
            result = {**result, 'characters': sorted(merged.values(), key=lambda c: -c['score'])}

    return result


def suggest_tags_multi_threshold(image_bytes, character_thresholds, general_threshold=0.35,
                                  general_limit=15, model_repo=None, backend='onnx'):
    """Dev/analysis helper (see the evaluate_threshold management command) —
    answers "what would suggest_tags() have returned at each of these
    character_threshold values" without re-running inference once per
    threshold. A threshold only decides which already-computed per-tag
    probabilities pass a cutoff — it doesn't change the forward pass itself
    — so this runs inference exactly once per image (plus once per detected
    person crop, same as suggest_tags()) and re-applies each threshold to
    that same cached prediction. Useful because whether the default
    character_threshold=0.85 is well-calibrated depends on the backend's own
    confidence distribution — a differently-trained model (e.g. 'timm') may
    need a different cutoff than the default ONNX model was tuned for.

    Returns {threshold: result_dict}, one entry per value in
    `character_thresholds`, each result_dict shaped exactly like
    suggest_tags()'s return value (mirrors its whole-image + multi-person-
    crop merge logic exactly, just parameterized over threshold instead of
    computed once).
    """
    whole_raw = _raw_predict(image_bytes, model_repo, backend)

    try:
        boxes = _detect_person_boxes(image_bytes)
    except Exception:
        logger.exception('Person detection failed — falling back to whole-image character tagging')
        boxes = []

    crop_raws = []
    if len(boxes) >= 2:
        for box in boxes[:_MAX_PERSON_CROPS]:
            try:
                crop_bytes = _crop_with_padding(image_bytes, box)
                crop_raws.append(_raw_predict(crop_bytes, model_repo, backend))
            except Exception:
                logger.exception('Per-crop tagging failed for box %s', box)

    results = {}
    for threshold in character_thresholds:
        result = _tags_from_predictions(*whole_raw, general_threshold, threshold, general_limit)
        if crop_raws:
            merged = {}
            for crop_raw in crop_raws:
                crop_result = _tags_from_predictions(*crop_raw, general_threshold, threshold, general_limit)
                for c in crop_result['characters']:
                    existing = merged.get(c['name'])
                    if existing is None or c['score'] > existing['score']:
                        merged[c['name']] = c
            if merged:
                result = {**result, 'characters': sorted(merged.values(), key=lambda c: -c['score'])}
        results[threshold] = result

    return results
