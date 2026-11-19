"""SAM 2.1 Hiera Small / Base+ (ONNX Runtime) instance segmentation for fanart_viewer.

Runs on CPU via `onnxruntime` + `huggingface_hub` (no extra PyTorch `sam2`
package required). Downloads the SAM 2.1 Hiera Small ONNX archive from
`vietanhdev/segment-anything-2.1-onnx-models` once on first use and caches it
under `tagger._data_dir()/sam2/<variant>/`.

Unlike SAM 1 / MobileSAM, SAM 2.1 feeds two high-resolution feature maps
(`high_res_feats_0` [1, 32, 256, 256] and `high_res_feats_1` [1, 64, 128, 128])
directly into the mask decoder alongside the main `image_embed` [1, 256, 64, 64],
yielding much cleaner boundaries on overlapping anime characters, hair strands,
and small mascots/chibis.

Environment overrides:
  - SAM2_VARIANT: 'small' (default), 'base_plus', or 'tiny'
  - SAM2_ONNX_REPO: default 'vietanhdev/segment-anything-2.1-onnx-models'
"""
import glob
import hashlib
import io
import logging
import os
import threading
import time
import zipfile
from collections import OrderedDict

import numpy as np

logger = logging.getLogger(__name__)

SAM2_ONNX_REPO = os.environ.get('SAM2_ONNX_REPO', 'vietanhdev/segment-anything-2.1-onnx-models')
SAM2_VARIANT = os.environ.get('SAM2_VARIANT', 'small').strip().lower()
SAM2_ARCHIVE_MAP = {
    'tiny': 'sam2.1_hiera_tiny_20260221.zip',
    'small': 'sam2.1_hiera_small_20260221.zip',
    'base_plus': 'sam2.1_hiera_base_plus_20260221.zip',
}
SAM2_INPUT_SIZE = 1024

# ImageNet mean/std in 0..1 scale (standard SAM 2 / Hiera preprocessing)
_IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

_lock = threading.Lock()
_sessions = None  # (encoder_sess, decoder_sess, variant_name)
_last_fail_ts = 0.0
_RETRY_COOLDOWN_SEC = 60.0
_embed_cache = OrderedDict()  # sha1(image_bytes) -> (enc_out_map, enc_out_by_shape, (orig_h, orig_w))
_EMBED_CACHE_MAX = 4


def _data_dir(variant=None):
    from item import tagger
    v = variant or SAM2_VARIANT
    d = os.path.join(tagger._data_dir(), 'sam2', v)
    os.makedirs(d, exist_ok=True)
    return d


def _find_extracted_onnx(extract_dir):
    enc_candidates = sorted(glob.glob(os.path.join(extract_dir, '**', '*encoder*.onnx'), recursive=True))
    dec_candidates = sorted(glob.glob(os.path.join(extract_dir, '**', '*decoder*.onnx'), recursive=True))
    if enc_candidates and dec_candidates:
        return enc_candidates[0], dec_candidates[0]
    return None, None


def _load_sessions():
    global _sessions, _last_fail_ts
    if _sessions is not None:
        return _sessions
    if _last_fail_ts and (time.monotonic() - _last_fail_ts) < _RETRY_COOLDOWN_SEC:
        return None
    with _lock:
        if _sessions is not None:
            return _sessions
        if _last_fail_ts and (time.monotonic() - _last_fail_ts) < _RETRY_COOLDOWN_SEC:
            return None
        try:
            import onnxruntime as ort
            from huggingface_hub import hf_hub_download

            variant = SAM2_VARIANT if SAM2_VARIANT in SAM2_ARCHIVE_MAP else 'small'
            archive_name = SAM2_ARCHIVE_MAP[variant]
            extract_dir = _data_dir(variant)

            enc_path, dec_path = _find_extracted_onnx(extract_dir)
            if not (enc_path and dec_path):
                zip_path = hf_hub_download(
                    repo_id=SAM2_ONNX_REPO,
                    filename=archive_name,
                    local_dir=extract_dir,
                )
                with zipfile.ZipFile(zip_path, 'r') as zf:
                    zf.extractall(extract_dir)
                enc_path, dec_path = _find_extracted_onnx(extract_dir)

            if not (enc_path and dec_path):
                raise FileNotFoundError(f'SAM 2.1 encoder/decoder ONNX files not found in {extract_dir}')

            opts = ort.SessionOptions()
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            providers = ['CPUExecutionProvider']
            enc_sess = ort.InferenceSession(enc_path, sess_options=opts, providers=providers)
            dec_sess = ort.InferenceSession(dec_path, sess_options=opts, providers=providers)
            _sessions = (enc_sess, dec_sess, f'sam2.1_{variant}')
            logger.info('Loaded SAM 2.1 (%s) ONNX sessions from %s', variant, extract_dir)
            return _sessions
        except Exception:
            _last_fail_ts = time.monotonic()
            logger.warning('SAM 2.1 ONNX sessions unavailable; falling back to non-SAM segmentation', exc_info=True)
            return None


def active_engine_name():
    return _sessions[2] if _sessions is not None else None


def _preprocess_image(image_rgb):
    """SAM 2.1 preprocessing: resize directly to 1024x1024 and ImageNet-normalize."""
    import cv2

    orig_h, orig_w = image_rgb.shape[:2]
    resized = cv2.resize(image_rgb, (SAM2_INPUT_SIZE, SAM2_INPUT_SIZE), interpolation=cv2.INTER_LINEAR)
    normed = (resized.astype(np.float32) / 255.0 - _IMAGENET_MEAN) / _IMAGENET_STD
    chw = np.transpose(normed, (2, 0, 1))
    return chw, (orig_h, orig_w)


def encode_image(image_bytes):
    """Compute (or retrieve cached) SAM 2.1 multi-scale image features for `image_bytes`.
    Returns `(out_by_name, out_by_shape, (orig_h, orig_w))` or None if unavailable.
    """
    from PIL import Image

    sess_tuple = _load_sessions()
    if sess_tuple is None:
        return None
    enc_sess = sess_tuple[0]

    key = hashlib.sha1(image_bytes).hexdigest()
    with _lock:
        if key in _embed_cache:
            _embed_cache.move_to_end(key)
            return _embed_cache[key]

    image = Image.open(io.BytesIO(image_bytes)).convert('RGB')
    img_rgb = np.asarray(image)
    chw, orig_size = _preprocess_image(img_rgb)

    enc_inp = enc_sess.get_inputs()[0]
    inp_tensor = chw[np.newaxis, :, :, :] if len(enc_inp.shape) == 4 else chw
    enc_outputs = enc_sess.run(None, {enc_inp.name: inp_tensor.astype(np.float32)})
    enc_names = [o.name for o in enc_sess.get_outputs()]

    out_by_name = {}
    out_by_shape = {}
    for name, arr in zip(enc_names, enc_outputs):
        a = np.asarray(arr, dtype=np.float32)
        out_by_name[name] = a
        out_by_shape[tuple(a.shape)] = a

    val = (out_by_name, out_by_shape, orig_size)
    with _lock:
        _embed_cache[key] = val
        _embed_cache.move_to_end(key)
        while len(_embed_cache) > _EMBED_CACHE_MAX:
            _embed_cache.popitem(last=False)
    return val


def _cast_for_ort_type(arr, ort_type):
    t = (ort_type or '').lower()
    if 'int64' in t:
        return np.asarray(arr, dtype=np.int64)
    if 'int32' in t:
        return np.asarray(arr, dtype=np.int32)
    return np.asarray(arr, dtype=np.float32)


def predict_mask(encoded, box=None, pos_points=None, neg_points=None):
    """Run the SAM 2.1 mask decoder on precomputed `encoded` features.
    Prompt labels follow standard SAM 2 convention:
      - 2 / 3: box top-left / bottom-right
      - 1: positive foreground point
      - 0: negative background/other-character point
    Returns uint8 `(orig_h, orig_w)` binary mask (0 or 255), or None.
    """
    import cv2

    sess_tuple = _load_sessions()
    if sess_tuple is None or encoded is None:
        return None
    dec_sess = sess_tuple[1]
    out_by_name, out_by_shape, (orig_h, orig_w) = encoded

    scale_x = SAM2_INPUT_SIZE / float(max(1, orig_w))
    scale_y = SAM2_INPUT_SIZE / float(max(1, orig_h))

    coords = []
    labels = []
    if box is not None and len(box) == 4:
        x1, y1, x2, y2 = (float(v) for v in box)
        coords.append([x1 * scale_x, y1 * scale_y])
        labels.append(2)
        coords.append([x2 * scale_x, y2 * scale_y])
        labels.append(3)
    for pt in (pos_points or ()):
        coords.append([float(pt[0]) * scale_x, float(pt[1]) * scale_y])
        labels.append(1)
    for pt in (neg_points or ()):
        coords.append([float(pt[0]) * scale_x, float(pt[1]) * scale_y])
        labels.append(0)

    if not coords:
        return None
    if box is None:
        coords.append([0.0, 0.0])
        labels.append(-1)

    point_coords = np.asarray(coords, dtype=np.float32)[np.newaxis, :, :]
    point_labels_raw = np.asarray(labels, dtype=np.float32)[np.newaxis, :]
    mask_input = np.zeros((1, 1, 256, 256), dtype=np.float32)
    has_mask_input = np.zeros((1,), dtype=np.float32)
    orig_im_size_raw = np.array([orig_h, orig_w], dtype=np.int32)

    # Resolve encoder feature tensors by exact name OR by canonical SAM 2.1 shape
    embed_256 = out_by_shape.get((1, 256, 64, 64))
    hr0_32 = out_by_shape.get((1, 32, 256, 256))
    hr1_64 = out_by_shape.get((1, 64, 128, 128))

    feed = {}
    for inp in dec_sess.get_inputs():
        name = inp.name
        low = name.lower()
        if name in out_by_name:
            feed[name] = out_by_name[name]
        elif 'high_res' in low and '0' in low and hr0_32 is not None:
            feed[name] = hr0_32
        elif 'high_res' in low and '1' in low and hr1_64 is not None:
            feed[name] = hr1_64
        elif ('embed' in low or 'pix_feat' in low) and embed_256 is not None:
            feed[name] = embed_256
        elif 'point_coords' in low or 'coords' in low:
            feed[name] = point_coords
        elif 'point_labels' in low or 'labels' in low:
            feed[name] = _cast_for_ort_type(point_labels_raw, inp.type)
        elif 'has_mask' in low:
            feed[name] = _cast_for_ort_type(has_mask_input, inp.type)
        elif 'mask_input' in low:
            feed[name] = mask_input
        elif 'orig_im_size' in low or 'im_size' in low:
            feed[name] = _cast_for_ort_type(orig_im_size_raw, inp.type)

    outputs = dec_sess.run(None, feed)
    out_names = [o.name for o in dec_sess.get_outputs()]
    out_map = dict(zip(out_names, outputs))

    masks = None
    iou_preds = None
    for name, arr in out_map.items():
        low = name.lower()
        if 'iou' in low or 'score' in low:
            iou_preds = np.asarray(arr)
        elif 'mask' in low and masks is None:
            masks = np.asarray(arr)
    if masks is None:
        masks = np.asarray(outputs[0])
    if iou_preds is None and len(outputs) > 1:
        iou_preds = np.asarray(outputs[1])

    # Normalize masks to 4D [B, M, H, W]
    if masks.ndim == 3:
        masks = masks[np.newaxis, :, :, :]

    best_idx = 0
    if iou_preds is not None and iou_preds.size > 1:
        scores = iou_preds.reshape(-1)
        best_idx = int(np.argmax(scores))

    chosen = masks[0, min(best_idx, masks.shape[1] - 1)].astype(np.float32)
    if chosen.shape[0] != orig_h or chosen.shape[1] != orig_w:
        chosen = cv2.resize(chosen, (orig_w, orig_h), interpolation=cv2.INTER_LINEAR)
    return np.where(chosen > 0.0, 255, 0).astype(np.uint8)


def segment_boxes(image_bytes, boxes, head_boxes=None):
    """Segment each detected character box using SAM 2.1 with BBox + own-head
    positive point + neighbor-head negative points.
    Returns list of `[{'box': [x1, y1, x2, y2], 'polygon': [[x, y], ...] | None}, ...]`
    or None if SAM 2.1 is unavailable.
    """
    from item import tagger

    if not boxes:
        return []
    encoded = encode_image(image_bytes)
    if encoded is None:
        return None

    _out_map, _out_shape, (img_h, img_w) = encoded
    clamped_boxes = []
    for box in boxes:
        x1, y1, x2, y2 = box
        bx1 = max(0, min(img_w, int(round(min(x1, x2)))))
        by1 = max(0, min(img_h, int(round(min(y1, y2)))))
        bx2 = max(0, min(img_w, int(round(max(x1, x2)))))
        by2 = max(0, min(img_h, int(round(max(y1, y2)))))
        clamped_boxes.append((bx1, by1, bx2, by2))

    head_list, head_owner, box_head = tagger._match_heads_to_boxes(clamped_boxes, head_boxes)

    regions = []
    for i, (bx1, by1, bx2, by2) in enumerate(clamped_boxes):
        if bx2 <= bx1 or by2 <= by1:
            regions.append({'box': [bx1, by1, bx2, by2], 'polygon': None})
            continue

        pos_pts = []
        own_head = box_head[i]
        if own_head is not None:
            pos_pts.append((0.5 * (own_head[0] + own_head[2]), 0.5 * (own_head[1] + own_head[3])))
        else:
            pos_pts.append((0.5 * (bx1 + bx2), by1 + 0.35 * (by2 - by1)))

        neg_pts = []
        for h_idx, (hx1, hy1, hx2, hy2) in enumerate(head_list):
            owner = head_owner[h_idx]
            if owner is not None and owner != i:
                neg_pts.append((0.5 * (hx1 + hx2), 0.5 * (hy1 + hy2)))

        mask = predict_mask(encoded, box=(bx1, by1, bx2, by2), pos_points=pos_pts, neg_points=neg_pts)
        if mask is None:
            regions.append({'box': [bx1, by1, bx2, by2], 'polygon': None})
            continue

        # Restrict mask to padded box so stray background fragments far away aren't included
        pad_x = int((bx2 - bx1) * 0.08)
        pad_y = int((by2 - by1) * 0.08)
        sx1, sy1 = max(0, bx1 - pad_x), max(0, by1 - pad_y)
        sx2, sy2 = min(img_w, bx2 + pad_x), min(img_h, by2 + pad_y)
        sub_mask = mask[sy1:sy2, sx1:sx2]

        poly, tight_box = tagger._mask_to_polygon(sub_mask, offset_x=sx1, offset_y=sy1)
        if poly is not None and tight_box is not None:
            regions.append({'box': tight_box, 'polygon': poly})
        else:
            regions.append({'box': [bx1, by1, bx2, by2], 'polygon': None})

    return regions


def segment_point(image_bytes, pos_points, neg_points=None, box=None):
    """Interactive segmentation from user click point(s) (+ optional negative points / box).
    Returns `{'box': [x1, y1, x2, y2], 'polygon': [[x, y], ...]}` or None.
    """
    from item import tagger

    encoded = encode_image(image_bytes)
    if encoded is None:
        return None
    mask = predict_mask(encoded, box=box, pos_points=pos_points, neg_points=neg_points)
    if mask is None:
        return None
    poly, tight_box = tagger._mask_to_polygon(mask, offset_x=0, offset_y=0)
    if poly is None or tight_box is None:
        return None
    return {'box': tight_box, 'polygon': poly}
