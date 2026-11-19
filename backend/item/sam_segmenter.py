"""Lightweight MobileSAM (ONNX Runtime) instance segmentation for fanart_viewer.

Runs entirely on CPU via `onnxruntime` + `huggingface_hub` (no extra PyTorch
SAM package required). Downloads `mobile_sam_image_encoder.onnx` (~28MB) and
`sam_mask_decoder_multi.onnx` (~16.5MB) from `Acly/MobileSAM` once on first
use and caches them under `tagger._data_dir()`.

Supports:
  - Automatic per-character segmentation from (person BBox + own head positive
    point + neighboring character heads as negative points).
  - Interactive one-click segmentation from user clicks in RegionAnnotator.jsx.
"""
import hashlib
import io
import logging
import os
import threading
from collections import OrderedDict

import numpy as np

logger = logging.getLogger(__name__)

SAM_ONNX_REPO = os.environ.get('SAM_ONNX_REPO', 'Acly/MobileSAM')
SAM_ENCODER_FILE = os.environ.get('SAM_ENCODER_FILE', 'mobile_sam_image_encoder.onnx')
SAM_DECODER_FILE = os.environ.get('SAM_DECODER_FILE', 'sam_mask_decoder_multi.onnx')
SAM_TARGET_LENGTH = 1024

# Standard SAM pixel normalization constants (RGB 0..255 scale)
_PIXEL_MEAN = np.array([123.675, 116.28, 103.53], dtype=np.float32)
_PIXEL_STD = np.array([58.395, 57.12, 57.375], dtype=np.float32)

_lock = threading.Lock()
_sessions = None  # (encoder_sess, decoder_sess) or False if permanently failed
_embed_cache = OrderedDict()  # sha1(image_bytes) -> (embeddings, (orig_h, orig_w), scale, (new_h, new_w))
_EMBED_CACHE_MAX = 4


def _data_dir():
    from item import tagger
    d = os.path.join(tagger._data_dir(), 'sam')
    os.makedirs(d, exist_ok=True)
    return d


def _load_sessions():
    global _sessions
    if _sessions is not None:
        return _sessions if _sessions is not False else None
    with _lock:
        if _sessions is not None:
            return _sessions if _sessions is not False else None
        try:
            import onnxruntime as ort
            from huggingface_hub import hf_hub_download

            cache_dir = _data_dir()
            enc_path = hf_hub_download(repo_id=SAM_ONNX_REPO, filename=SAM_ENCODER_FILE, local_dir=cache_dir)
            dec_path = hf_hub_download(repo_id=SAM_ONNX_REPO, filename=SAM_DECODER_FILE, local_dir=cache_dir)

            opts = ort.SessionOptions()
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            providers = ['CPUExecutionProvider']
            enc_sess = ort.InferenceSession(enc_path, sess_options=opts, providers=providers)
            dec_sess = ort.InferenceSession(dec_path, sess_options=opts, providers=providers)
            _sessions = (enc_sess, dec_sess)
            logger.info('Loaded MobileSAM ONNX sessions from %s', cache_dir)
            return _sessions
        except Exception:
            logger.warning('MobileSAM ONNX sessions unavailable; falling back to non-SAM segmentation', exc_info=True)
            _sessions = False
            return None


def _preprocess_image(image_rgb):
    """Resize longest side to 1024, ImageNet-normalize, and zero-pad to 1024x1024."""
    import cv2

    orig_h, orig_w = image_rgb.shape[:2]
    scale = SAM_TARGET_LENGTH / float(max(orig_h, orig_w))
    new_h = max(1, int(round(orig_h * scale)))
    new_w = max(1, int(round(orig_w * scale)))
    resized = cv2.resize(image_rgb, (new_w, new_h), interpolation=cv2.INTER_LINEAR).astype(np.float32)
    normed = (resized - _PIXEL_MEAN) / _PIXEL_STD

    padded = np.zeros((SAM_TARGET_LENGTH, SAM_TARGET_LENGTH, 3), dtype=np.float32)
    padded[:new_h, :new_w, :] = normed
    chw = np.transpose(padded, (2, 0, 1))
    return chw, (orig_h, orig_w), scale, (new_h, new_w)


def encode_image(image_bytes):
    """Compute (or retrieve cached) MobileSAM image embeddings for `image_bytes`.
    Returns `(embeddings, (orig_h, orig_w), scale, (new_h, new_w))` or None if unavailable.
    """
    from PIL import Image

    sess_pair = _load_sessions()
    if sess_pair is None:
        return None
    enc_sess, _dec_sess = sess_pair

    key = hashlib.sha1(image_bytes).hexdigest()
    with _lock:
        if key in _embed_cache:
            _embed_cache.move_to_end(key)
            return _embed_cache[key]

    image = Image.open(io.BytesIO(image_bytes)).convert('RGB')
    img_rgb = np.asarray(image)
    chw, orig_size, scale, new_size = _preprocess_image(img_rgb)

    enc_inp = enc_sess.get_inputs()[0]
    inp_tensor = chw[np.newaxis, :, :, :] if len(enc_inp.shape) == 4 else chw
    embeddings = enc_sess.run(None, {enc_inp.name: inp_tensor.astype(np.float32)})[0]
    if embeddings.ndim == 3:
        embeddings = embeddings[np.newaxis, :, :, :]

    val = (embeddings, orig_size, scale, new_size)
    with _lock:
        _embed_cache[key] = val
        _embed_cache.move_to_end(key)
        while len(_embed_cache) > _EMBED_CACHE_MAX:
            _embed_cache.popitem(last=False)
    return val


def predict_mask(encoded, box=None, pos_points=None, neg_points=None):
    """Run the MobileSAM mask decoder on precomputed `encoded` features.
    Prompt labels follow standard SAM convention:
      - 2.0 / 3.0: box top-left / bottom-right
      - 1.0: positive foreground point
      - 0.0: negative background/other-character point
      - -1.0: padding point (appended when only point prompts are supplied)
    Returns uint8 `(orig_h, orig_w)` binary mask (0 or 255), or None.
    """
    import cv2

    sess_pair = _load_sessions()
    if sess_pair is None or encoded is None:
        return None
    _enc_sess, dec_sess = sess_pair
    embeddings, (orig_h, orig_w), scale, (new_h, new_w) = encoded

    coords = []
    labels = []
    if box is not None and len(box) == 4:
        x1, y1, x2, y2 = (float(v) for v in box)
        coords.append([x1 * scale, y1 * scale])
        labels.append(2.0)
        coords.append([x2 * scale, y2 * scale])
        labels.append(3.0)
    for pt in (pos_points or ()):
        coords.append([float(pt[0]) * scale, float(pt[1]) * scale])
        labels.append(1.0)
    for pt in (neg_points or ()):
        coords.append([float(pt[0]) * scale, float(pt[1]) * scale])
        labels.append(0.0)

    if not coords:
        return None
    if box is None:
        # Standard SAM ONNX decoder expects a trailing padding point when no box is provided
        coords.append([0.0, 0.0])
        labels.append(-1.0)

    point_coords = np.asarray(coords, dtype=np.float32)[np.newaxis, :, :]
    point_labels = np.asarray(labels, dtype=np.float32)[np.newaxis, :]
    mask_input = np.zeros((1, 1, 256, 256), dtype=np.float32)
    has_mask_input = np.zeros((1,), dtype=np.float32)
    orig_im_size = np.array([float(orig_h), float(orig_w)], dtype=np.float32)

    all_inputs = {
        'image_embeddings': embeddings.astype(np.float32),
        'point_coords': point_coords,
        'point_labels': point_labels,
        'mask_input': mask_input,
        'has_mask_input': has_mask_input,
        'orig_im_size': orig_im_size,
    }
    dec_input_names = {i.name for i in dec_sess.get_inputs()}
    feed = {k: v for k, v in all_inputs.items() if k in dec_input_names}

    outputs = dec_sess.run(None, feed)
    out_names = [o.name for o in dec_sess.get_outputs()]
    out_map = dict(zip(out_names, outputs))

    masks = out_map.get('masks', outputs[0])
    iou_preds = out_map.get('iou_predictions', outputs[1] if len(outputs) > 1 else None)
    low_res = out_map.get('low_res_masks', outputs[2] if len(outputs) > 2 else None)

    # Pick the highest-IoU candidate mask (skip index 0 in 4-mask multi output only if
    # there are 4 masks and box is None? Actually argmax over iou_predictions is standard
    # and reliable for both single and multi decoders).
    best_idx = 0
    if iou_preds is not None and iou_preds.size > 1:
        scores = iou_preds.reshape(-1)
        best_idx = int(np.argmax(scores))

    if masks is not None and masks.ndim == 4 and masks.shape[2] == orig_h and masks.shape[3] == orig_w:
        chosen = masks[0, min(best_idx, masks.shape[1] - 1)]
        return np.where(chosen > 0.0, 255, 0).astype(np.uint8)

    # Upscale from 256x256 low-res logits by cropping the padded region first
    src_masks = low_res if low_res is not None else masks
    if src_masks is None:
        return None
    chosen = src_masks[0, min(best_idx, src_masks.shape[1] - 1)]
    lh, lw = chosen.shape[:2]
    valid_h = max(1, int(round(new_h * lh / float(SAM_TARGET_LENGTH))))
    valid_w = max(1, int(round(new_w * lw / float(SAM_TARGET_LENGTH))))
    unpadded = chosen[:valid_h, :valid_w]
    upscaled = cv2.resize(unpadded, (orig_w, orig_h), interpolation=cv2.INTER_LINEAR)
    return np.where(upscaled > 0.0, 255, 0).astype(np.uint8)


def segment_boxes(image_bytes, boxes, head_boxes=None):
    """Segment each detected character box using MobileSAM with BBox + own-head
    positive point + neighbor-head negative points.
    Returns list of `[{'box': [x1, y1, x2, y2], 'polygon': [[x, y], ...] | None}, ...]`
    or None if MobileSAM is unavailable.
    """
    from item import tagger

    if not boxes:
        return []
    encoded = encode_image(image_bytes)
    if encoded is None:
        return None

    _emb, (img_h, img_w), _scale, _new_size = encoded
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
