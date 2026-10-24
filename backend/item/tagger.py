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

# Danbooru/WD14 tag vocabulary for "more than one person" compositions.
# MULTIPLE briefly used the person detector's own box count instead (see
# _detect_person_boxes) — reverted back to tags after that produced too
# many false positives in practice: the detector counts bounding BOXES,
# not distinct identities, so the SAME character drawn twice in one image
# (a before/after split, a multi-pose/expression sheet — Danbooru's own
# "multiple_views" tag covers exactly this) was routinely counted as 2+
# people. Tag-based detection isn't perfect either (a tag-based miss on a
# genuinely multi-person image, or a "3+girls" tag firing on an incidental
# background crowd), but those false negatives/positives were judged less
# disruptive in practice than the detector's false positives.
_MULTIPLE_PERSON_TAGS = {
    '2girls', '3girls', '4girls', '5girls', '6+girls', 'multiple_girls',
    '2boys', '3boys', '4boys', '5boys', '6+boys', 'multiple_boys',
    '2others', '3others', 'multiple_others',
}


def _situation_hint(rating, general_tag_names):
    """Derive a single situation_hint from the rating + the tagger's own
    people-count tags (see _MULTIPLE_PERSON_TAGS's own comment for why
    this is tag-based rather than person-detector-box-count-based).

    R18 takes priority when the rating implies it — `situation` is a single
    value in this app's model, and content warning takes precedence over
    composition.
    """
    if rating in RATING_TO_SITUATION_HINT:
        return RATING_TO_SITUATION_HINT[rating]

    names = set(general_tag_names)
    if names & _MULTIPLE_PERSON_TAGS:
        return 'MULTIPLE'
    if 'solo' in names and '1girl' in names:
        return 'SOLO'
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


# imgutils is only ever imported lazily (find_spec here, like HAVE_TIMM), and
# CCIP's own ONNX model is fetched through huggingface_hub on first use --
# routed into this app's own data dir for the same reason _load_timm does it
# (the default ~/.cache may not survive a container restart).
HAVE_CCIP = importlib.util.find_spec('imgutils') is not None
CCIP_DIM = 768
_ccip_importable = None


def ccip_available():
    """Whether CCIP can actually be imported here -- HAVE_CCIP only says the
    imgutils package is installed, which is true of builds (the exe installs
    it with --no-deps) where the CCIP module's own imports may still fail.
    Checked once and cached; the model download itself happens on first use."""
    global _ccip_importable
    if _ccip_importable is None:
        try:
            os.environ.setdefault('HF_HOME', os.path.join(_data_dir(), 'hf_cache'))
            from imgutils.metrics import ccip_extract_feature  # noqa: F401
            _ccip_importable = True
        except Exception:
            logger.exception('CCIP is not importable in this environment')
            _ccip_importable = False
    return _ccip_importable


def ccip_feature(image_bytes):
    """CCIP (deepghs' Character Contrastive Image Pretraining, via imgutils)
    embedding of an image -- a 768-d vector trained so that two pictures of
    the SAME anime character land close together regardless of outfit, pose
    or scene. Unlike the tagger it never goes through a tag vocabulary.
    Expects roughly one character (a person crop, or a solo image)."""
    if not (HAVE_CCIP and ccip_available()):
        raise RuntimeError('ccip_feature needs dghs-imgutils with its CCIP module importable (see requirements.txt)')
    os.environ.setdefault('HF_HOME', os.path.join(_data_dir(), 'hf_cache'))
    from imgutils.metrics import ccip_extract_feature
    from PIL import Image

    image = Image.open(io.BytesIO(image_bytes))
    canvas = Image.new('RGBA', image.size, (255, 255, 255))
    canvas.alpha_composite(image.convert('RGBA'))
    return np.asarray(ccip_extract_feature(canvas.convert('RGB')), dtype=np.float32)


def to_grayscale_bytes(image_bytes):
    """The same image with its colour removed (alpha flattened onto white
    first, exactly like the tagger's own preprocessing), re-encoded as PNG.
    Used to give the character classifier black-and-white versions of its
    training images -- see train_character_classifier's --grayscale-augment."""
    from PIL import Image

    image = Image.open(io.BytesIO(image_bytes))
    canvas = Image.new('RGBA', image.size, (255, 255, 255))
    canvas.alpha_composite(image.convert('RGBA'))
    buf = io.BytesIO()
    canvas.convert('L').convert('RGB').save(buf, format='PNG')
    return buf.getvalue()


def color_stats(image_bytes):
    """{'mean_saturation', 'colorful_fraction'} of the image's visible
    (non-near-black) pixels, on a small thumbnail -- cheap (milliseconds),
    independent of the tagger. A black-and-white drawing sits near 0 on
    both; a coloured one doesn't."""
    from PIL import Image

    image = Image.open(io.BytesIO(image_bytes))
    image.draft('RGB', (320, 320))
    rgba = image.convert('RGBA')
    canvas = Image.new('RGBA', rgba.size, (255, 255, 255))
    canvas.alpha_composite(rgba)
    thumb = canvas.convert('RGB')
    thumb.thumbnail((256, 256))
    hsv = np.asarray(thumb.convert('HSV'), dtype=np.float32) / 255.0
    sat, val = hsv[..., 1], hsv[..., 2]
    visible = val > 0.08
    if not visible.any():
        return {'mean_saturation': 0.0, 'colorful_fraction': 0.0}
    return {
        'mean_saturation': float(sat[visible].mean()),
        'colorful_fraction': float((sat[visible] > 0.15).mean()),
    }


# Calibrated on ~8200 of this archive's own training images: at these values 6.4% of them
# count as monochrome, and 80% of those also carry the tagger's own "monochrome"/"greyscale"
# tag; loosening either bound sweeps in pastel and sepia-tinted colour work (the
# agreement falls to ~55% by sat<=0.06).
MONO_MAX_MEAN_SATURATION = 0.02
MONO_MAX_COLORFUL_FRACTION = 0.03


def is_monochrome(image_bytes):
    """Whether the image is (effectively) black-and-white. Tolerates the
    faint tint a scan or JPEG leaves on a grey drawing; a drawing with a
    spot of colour (a red ribbon) isn't monochrome, since its colour cues
    -- the very thing this flag exists to say are missing -- are present."""
    try:
        stats = color_stats(image_bytes)
    except Exception:
        return False
    return (stats['mean_saturation'] <= MONO_MAX_MEAN_SATURATION
            and stats['colorful_fraction'] <= MONO_MAX_COLORFUL_FRACTION)


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


def _timm_forward(image_bytes, model_repo, with_embedding=False):
    """One forward pass -> (tag probabilities, loaded state, embedding|None).
    With `with_embedding`, the pooled pre-logit embedding comes out of the
    SAME pass (forward_features once, then the head twice -- the head is
    negligible next to the backbone), so a classifier that wants the
    embedding costs no second inference."""
    repo = model_repo or TIMM_MODEL_REPO
    state = _load_timm(repo)  # sets HF_HOME and imports torch/timm as a side effect, before this next import
    import torch
    tensor = _prepare_image_timm(image_bytes, state['transform'])

    with torch.inference_mode():
        embedding = None
        if with_embedding:
            features = state['model'].forward_features(tensor)
            embedding = state['model'].forward_head(features, pre_logits=True).squeeze(0).numpy().astype(np.float32)
            logits = state['model'].forward_head(features)
        else:
            logits = state['model'](tensor)
        preds = torch.sigmoid(logits).squeeze(0).numpy()
    return preds, state, embedding


def _raw_predict_timm(image_bytes, model_repo):
    preds, state, _embedding = _timm_forward(image_bytes, model_repo)
    return preds, state['tag_names'], state['rating_idx'], state['general_idx'], state['character_idx']


def extract_embedding(image_bytes, model_repo=None):
    """The canary (timm/EVA02) backend's pooled pre-logit embedding for
    this image — the same forward pass as _raw_predict_timm, up to but not
    including its final classification head. This retains visual
    information the tag-probability output has already thrown away (the
    tag vocabulary is a bottleneck by design), which is why a downstream
    classifier trained on THIS instead of the tag probabilities is
    expected to do better — see train_character_classifier's
    --classifier/architecture-comparison notes.

    ONNX has no equivalent here (its published graph ends at the tag
    logits — there's no accessible intermediate layer without surgically
    editing the ONNX graph, which isn't worth doing for an experiment);
    only the canary/timm backend supports this.
    """
    if not HAVE_TIMM:
        raise RuntimeError(
            "extract_embedding needs the 'timm' backend (torch/timm installed) — see "
            'requirements-timm.txt / the INSTALL_TIMM_TAGGER build arg.'
        )
    repo = model_repo or TIMM_MODEL_REPO
    state = _load_timm(repo)
    import torch
    tensor = _prepare_image_timm(image_bytes, state['transform'])
    with torch.inference_mode():
        features = state['model'].forward_features(tensor)
        embedding = state['model'].forward_head(features, pre_logits=True)
    return embedding.squeeze(0).numpy().astype(np.float32)


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


def _raw_predict_with_embedding(image_bytes, model_repo, backend):
    """(the usual 5-tuple of _raw_predict, embedding|None). The ONNX backend
    has no accessible embedding (see extract_embedding), so it yields None."""
    if backend == 'timm':
        preds, state, embedding = _timm_forward(image_bytes, model_repo, with_embedding=True)
        return (preds, state['tag_names'], state['rating_idx'], state['general_idx'], state['character_idx']), embedding
    return _raw_predict_onnx(image_bytes, model_repo), None


def _suggest_tags_single_pass(image_bytes, general_threshold, character_threshold, general_limit, model_repo, backend,
                               with_embedding=False):
    if with_embedding:
        (preds, tag_names, rating_idx, general_idx, character_idx), embedding = _raw_predict_with_embedding(
            image_bytes, model_repo, backend,
        )
    else:
        preds, tag_names, rating_idx, general_idx, character_idx = _raw_predict(image_bytes, model_repo, backend)
        embedding = None
    result = _tags_from_predictions(
        preds, tag_names, rating_idx, general_idx, character_idx,
        general_threshold, character_threshold, general_limit,
    )
    # Internal-only — the raw (pre-threshold) general-tag probability
    # vector, for suggest_tags() to surface as 'general_probs' on the
    # WHOLE-IMAGE pass only (see character_classifier.py's feature
    # representation, trained on this exact vector). Popped before
    # suggest_tags()'s return value, and never read from a per-crop pass —
    # see suggest_tags's own docstring for why.
    result['_general_probs'] = np.asarray(preds, dtype=np.float32)[general_idx]
    result['_embedding'] = embedding
    return result


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


_HEAD_DETECT_CONF_THRESHOLD = 0.3


def _detect_head_boxes(image_bytes):
    """[(x1, y1, x2, y2), ...] in the original image's pixel coordinates,
    most-confident first — same contract as _detect_person_boxes (never
    raises; empty list on failure), but for the anime HEAD detector
    (dghs-imgutils's deepghs/anime_head_detection) instead of the person
    detector. Its box covers the entire head silhouette including hair —
    "include the entire head, not only the face parts" per the model's own
    dataset description — deliberately NOT a tight face-only crop, since
    hair color/style is one of the most stable identifying traits for an
    anime character design (unlike real human hair, which isn't identity-
    stable and is why real face-recognition pipelines crop it out).

    This exists as an alternative crop source to _detect_person_boxes for
    the character classifier: a person-box includes the costume, which
    changes across outfits for the "same" character and can teach the
    classifier to key on clothing rather than identity; a head-box crops
    that out entirely while keeping the face+hair region the person-box
    also contains, so it's a strict subset of what the person-box already
    captures, not a different modality. Verified live against a real
    2-character DB image — produced exactly 2 correctly-separated head
    boxes matching the item's confirmed character count.
    """
    from imgutils.detect import detect_heads
    from PIL import Image

    image = Image.open(io.BytesIO(image_bytes)).convert('RGB')
    results = detect_heads(image, conf_threshold=_HEAD_DETECT_CONF_THRESHOLD)
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
                  model_repo=None, backend='onnx', with_embedding=False, with_ccip=False):
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

    Also includes `general_probs` (the whole image's raw general-tag
    probability vector — see character_classifier.py, which trains and
    predicts on exactly this) and `person_count` (how many boxes the
    person detector found, used only to gate the per-crop character
    re-derivation above — NOT consulted for `situation_hint`, which is
    tag-based; see _situation_hint's own comment for why) — additive
    fields, safe for any existing caller that only reads the keys
    documented above.

    `with_embedding`: also return the pooled embedding ('embedding', canary/
    timm backend only; None otherwise) for the whole image and for every
    person crop -- from the SAME forward pass that already runs, no extra
    inference. `crops` lists, per detected person (up to _MAX_PERSON_CROPS,
    only when 2+ people were found), that crop's own 'general_probs'/
    'embedding'/'box' -- the input the supplementary character classifier
    needs on a multi-person image. It was trained on person crops (manually
    labeled regions), so feeding it the blended whole-image vector of a
    group shot would be a train/inference mismatch.
    """
    try:
        boxes = _detect_person_boxes(image_bytes)
    except Exception:
        logger.exception('Person detection failed — falling back to whole-image character tagging')
        boxes = []
    person_count = len(boxes)

    result = _suggest_tags_single_pass(
        image_bytes, general_threshold, character_threshold, general_limit, model_repo, backend,
        with_embedding=with_embedding,
    )
    general_probs = result.pop('_general_probs')
    embedding = result.pop('_embedding', None)
    ccip = _safe_ccip(image_bytes) if with_ccip else None

    crops = []
    if len(boxes) >= 2:
        merged = {}
        for box in boxes[:_MAX_PERSON_CROPS]:
            try:
                crop_bytes = _crop_with_padding(image_bytes, box)
                crop_result = _suggest_tags_single_pass(
                    crop_bytes, general_threshold, character_threshold, general_limit, model_repo, backend,
                    with_embedding=with_embedding,
                )
            except Exception:
                logger.exception('Per-crop tagging failed for box %s', box)
                continue
            crops.append({
                'box': list(box),
                'characters': crop_result.get('characters', []),
                'general_probs': crop_result.pop('_general_probs'),
                'embedding': crop_result.pop('_embedding', None),
                'ccip': _safe_ccip(crop_bytes) if with_ccip else None,
            })
            for c in crop_result['characters']:
                existing = merged.get(c['name'])
                if existing is None or c['score'] > existing['score']:
                    merged[c['name']] = c
        if merged:
            # Crop-derived characters supersede (not merge with) the
            # whole-image pass's — that whole-image list is exactly the
            # cross-contamination-prone signal this is meant to replace.
            result = {**result, 'characters': sorted(merged.values(), key=lambda c: -c['score'])}

    result['general_probs'] = general_probs
    result['embedding'] = embedding
    result['ccip'] = ccip
    result['crops'] = crops
    result['boxes'] = [list(b) for b in boxes]
    result['person_count'] = person_count
    return result


def _safe_ccip(image_bytes):
    try:
        return ccip_feature(image_bytes)
    except Exception:
        logger.exception('CCIP feature extraction failed')
        return None


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

    person_count = len(boxes)

    crop_raws = []
    if person_count >= 2:
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
        result['person_count'] = person_count
        results[threshold] = result

    return results


# --- Supplementary character classifier ------------------------------------
#
# A lightweight classifier trained on this app's OWN labeled images (see
# item.management.commands.train_character_classifier) for characters the
# Danbooru-trained tagger backends structurally cannot know about (OCs,
# characters from titles not yet in Danbooru's tag vocabulary at all — see
# that command's docstring for concrete examples this app hit). Reuses
# suggest_tags()'s own 'general_probs' as its feature vector — no extra
# inference cost, since that's already computed for every suggest_tags()
# call regardless.
#
# v1 scope: trained only on single-character images, so a caller should
# only trust a prediction when suggest_tags() found 0-1 people in the
# image (its 'person_count') — feeding a multi-character image's blended
# features into a classifier that never saw more than one character during
# training would be a meaningless extrapolation, not a real prediction.
# item.views._collect_candidates enforces this at the call site.

_CHARACTER_CLASSIFIER_MIN_CONFIDENCE = 0.5
_classifier_state = {}  # backend -> loaded artifact dict, or None once confirmed missing/unloadable


def _load_character_classifier(backend):
    if backend in _classifier_state:
        return _classifier_state[backend]
    with _lock:
        if backend in _classifier_state:
            return _classifier_state[backend]
        backend_choice = 'canary' if backend == 'timm' else 'onnx'
        path = os.path.join(_data_dir(), f'character_classifier_{backend_choice}.joblib')
        artifact = None
        if os.path.exists(path):
            try:
                import joblib
                artifact = joblib.load(path)
            except Exception:
                logger.exception('Failed to load character classifier from %s', path)
                artifact = None
        _classifier_state[backend] = artifact
        return artifact


def have_character_classifier(backend):
    """Whether a trained classifier artifact is available for this backend
    — lets a caller skip the (cheap, but non-zero) predict_character call
    entirely, and tells evaluate_ensemble/UI code whether this source can
    contribute anything at all."""
    return _load_character_classifier(backend) is not None


def feature_parts(feature_source):
    """'tags+ccip' -> ['tags', 'ccip']; the legacy 'both' means tags+embedding."""
    if feature_source == 'both':
        return ['tags', 'embedding']
    return [p for p in (feature_source or 'tags').split('+') if p]


def classifier_feature_source(backend):
    """What the trained artifact for this backend was fit on, e.g. 'tags',
    'embedding', 'ccip', 'tags+ccip' (None if there is no artifact). Lets a
    caller ask suggest_tags only for the features the classifier will use."""
    artifact = _load_character_classifier(backend)
    if artifact is None:
        return None
    return artifact.get('feature_source', 'tags')


def _classifier_probabilities(general_probs, backend, embedding, ccip):
    """(classes, probabilities) over every class the classifier knows, or
    None when it can't answer (no artifact, a needed feature missing, or a
    feature dimension that doesn't match what it was trained on -- fails safe
    rather than guessing)."""
    artifact = _load_character_classifier(backend)
    if artifact is None:
        return None
    clf = artifact['classifier']

    available = {'tags': general_probs, 'embedding': embedding, 'ccip': ccip}
    parts = []
    for name in feature_parts(artifact.get('feature_source', 'tags')):
        value = available.get(name)
        if value is None:
            return None
        tag_dim = artifact.get('tag_dim', len(artifact['general_tag_names']))
        if name == 'tags' and len(value) != tag_dim:
            logger.warning(
                'Character classifier feature-length mismatch (got %d, trained on %d) — '
                'the tag list likely changed since training; skipping prediction.',
                len(value), tag_dim,
            )
            return None
        parts.append(np.asarray(value, dtype=np.float32))
    feature = np.concatenate(parts) if len(parts) > 1 else parts[0]
    expected = artifact.get('feature_dim')
    if expected is not None and len(feature) != expected:
        logger.warning('Character classifier feature dim mismatch (got %d, trained on %d); skipping.', len(feature), expected)
        return None
    return clf.classes_, clf.predict_proba(feature.reshape(1, -1))[0]


def predict_character_topk(general_probs, backend, k=3, floor=0.02, embedding=None, ccip=None):
    """[(character, probability), ...] best first: the classifier's top `k`
    classes whose probability is at least `floor` ([] if it can't answer).

    This -- not predict_character's single thresholded answer -- is what the
    suggestion ensemble consumes. Measured (item-grouped CV, 8.2k labelled
    images): handing over only the top-1 above 0.5 withheld correct but
    less-certain answers, so a post's character was the top suggestion 79.9%
    of the time for single-image posts; the top 3 with a small floor makes it
    89.2% (top-3 contains it 96%). Candidates are for a person to review, so
    an uncertain-but-right one in the list beats none. A floor of 0.02 gave
    the same accuracy as no floor with ~1/3 fewer candidates.

    `general_probs`/`embedding`/`ccip` must be suggest_tags()'s own values
    for THIS SAME backend and for the SAME image or crop (the artifact's own
    `feature_source` decides which are used)."""
    answer = _classifier_probabilities(general_probs, backend, embedding, ccip)
    if answer is None:
        return []
    classes, probs = answer
    order = np.argsort(-probs)[:k]
    return [(classes[i], float(probs[i])) for i in order if probs[i] >= floor]


def predict_character(general_probs, backend, min_confidence=_CHARACTER_CLASSIFIER_MIN_CONFIDENCE,
                      embedding=None, ccip=None):
    """(character_name, confidence) of the classifier's single best class, or
    (None, 0.0) if it can't answer, or (None, confidence) if the best class
    doesn't clear min_confidence. Kept for the single-answer cascade and the
    evaluation commands; the ensemble uses predict_character_topk."""
    answer = _classifier_probabilities(general_probs, backend, embedding, ccip)
    if answer is None:
        return None, 0.0
    classes, probs = answer
    idx = int(np.argmax(probs))
    confidence = float(probs[idx])
    if confidence < min_confidence:
        return None, confidence
    return classes[idx], confidence
