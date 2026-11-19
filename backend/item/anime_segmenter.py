"""Anime-specialized two-stage instance segmentation (ONNX Runtime).

Uses `dreMaz/AnimeInstanceSegmentation` ONNX weights hosted at
`Faor-Mati/anime-character-segmentation`:
  1. Stage 1 (`anime_segmentor_rtmdet_e60_simplified.onnx`):
     RTMDet-Ins (CondInst-style) anime character instance detector/segmenter.
     Detects multiple overlapping anime characters and predicts per-instance
     dynamic convolution kernels to produce individual coarse masks.
  2. Stage 2 (`mask_refiner_isnetdis_refine_last_simplified.onnx`):
     ISNet-DIS 4-channel (RGB + coarse mask) refiner that sharpens each
     character's boundary along anime hair strands, lineart, and clothing.

Runs on CPU via `onnxruntime` + `numpy` + `cv2` + `PIL` (no PyTorch or
mmcv/mmdet dependency required). Models download once on first use into
`tagger._data_dir()/anime_ins/`.
"""
import io
import logging
import os
import threading
import time

import numpy as np

logger = logging.getLogger(__name__)

ANIME_SEG_REPO = os.environ.get('ANIME_SEG_REPO', 'Faor-Mati/anime-character-segmentation')
SEG_FILENAME = 'anime_segmentor_rtmdet_e60_simplified.onnx'
REFINE_FILENAME = 'mask_refiner_isnetdis_refine_last_simplified.onnx'

# MMDetection RTMDet BGR normalization constants
_RTMDET_MEAN = np.array([123.675, 116.28, 103.53], dtype=np.float32)
_RTMDET_STD = np.array([58.395, 57.12, 57.375], dtype=np.float32)

_lock = threading.Lock()
_sessions = None  # (seg_sess, refine_sess_or_None, engine_name)
_last_fail_ts = 0.0
_RETRY_COOLDOWN_SEC = 60.0


def _data_dir():
    from item import tagger
    d = os.path.join(tagger._data_dir(), 'anime_ins')
    os.makedirs(d, exist_ok=True)
    return d


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

            model_dir = _data_dir()
            seg_path = os.path.join(model_dir, SEG_FILENAME)
            if not os.path.isfile(seg_path):
                seg_path = hf_hub_download(
                    repo_id=ANIME_SEG_REPO,
                    filename=SEG_FILENAME,
                    local_dir=model_dir,
                )

            refine_path = os.path.join(model_dir, REFINE_FILENAME)
            if not os.path.isfile(refine_path):
                try:
                    refine_path = hf_hub_download(
                        repo_id=ANIME_SEG_REPO,
                        filename=REFINE_FILENAME,
                        local_dir=model_dir,
                    )
                except Exception:
                    logger.warning('AnimeInstanceSegmentation refiner download failed; running coarse stage only', exc_info=True)
                    refine_path = None

            opts = ort.SessionOptions()
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            providers = ['CPUExecutionProvider']
            seg_sess = ort.InferenceSession(seg_path, sess_options=opts, providers=providers)

            refine_sess = None
            if refine_path and os.path.isfile(refine_path):
                try:
                    refine_sess = ort.InferenceSession(refine_path, sess_options=opts, providers=providers)
                except Exception:
                    logger.warning('AnimeInstanceSegmentation refiner load failed; running coarse stage only', exc_info=True)

            engine_name = 'anime_ins_refined' if refine_sess is not None else 'anime_ins_coarse'
            _sessions = (seg_sess, refine_sess, engine_name)
            logger.info('Loaded AnimeInstanceSegmentation (%s) ONNX sessions from %s', engine_name, model_dir)
            return _sessions
        except Exception:
            _last_fail_ts = time.monotonic()
            logger.warning('AnimeInstanceSegmentation ONNX sessions unavailable', exc_info=True)
            return None


def active_engine_name():
    return _sessions[2] if _sessions is not None else None


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -50.0, 50.0)))


def _resize_pad(img_pil, size, pad_value):
    from PIL import Image

    w, h = img_pil.size
    scale = float(size) / float(max(1, max(w, h)))
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    resized = img_pil.resize((new_w, new_h), Image.BILINEAR)
    pad_l = (size - new_w) // 2
    pad_t = (size - new_h) // 2
    pad_r = size - new_w - pad_l
    pad_b = size - new_h - pad_t
    canvas = Image.new(img_pil.mode, (size, size), pad_value)
    canvas.paste(resized, (pad_l, pad_t))
    return canvas, (pad_t, pad_b, pad_l, pad_r), (new_w, new_h)


def _nms_boxes(boxes, scores, iou_threshold):
    """Greedy IoU NMS on `[N, 4]` xyxy boxes."""
    if len(boxes) == 0:
        return []
    boxes = np.asarray(boxes, dtype=np.float32)
    scores = np.asarray(scores, dtype=np.float32)
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(x1[i], x1[rest])
        yy1 = np.maximum(y1[i], y1[rest])
        xx2 = np.minimum(x2[i], x2[rest])
        yy2 = np.minimum(y2[i], y2[rest])
        inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        denom = areas[i] + areas[rest] - inter
        iou = np.where(denom > 0.0, inter / denom, 0.0)
        order = rest[iou <= iou_threshold]
    return keep


def _apply_dynamic_conv(mask_feat, kernel, proto_channels):
    """Apply CondInst 3-layer 1x1 dynamic convolution in pure NumPy.

    `mask_feat`: float32 `(proto_channels + 2, Hp, Wp)`
    `kernel`: float32 `(flatten_dim,)`
    Returns float32 logits `(Hp, Wp)`.
    """
    in_ch = proto_channels + 2
    inter = 8
    w_nums = (inter * in_ch, inter * inter, 1 * inter)
    b_nums = (inter, inter, 1)
    expected = sum(w_nums) + sum(b_nums)
    if kernel.size < expected:
        raise ValueError(f'Unexpected CondInst kernel length {kernel.size} < {expected}')

    idx = 0
    w0 = kernel[idx:idx + w_nums[0]].reshape(inter, in_ch); idx += w_nums[0]
    w1 = kernel[idx:idx + w_nums[1]].reshape(inter, inter); idx += w_nums[1]
    w2 = kernel[idx:idx + w_nums[2]].reshape(1, inter); idx += w_nums[2]
    b0 = kernel[idx:idx + b_nums[0]]; idx += b_nums[0]
    b1 = kernel[idx:idx + b_nums[1]]; idx += b_nums[1]
    b2 = kernel[idx:idx + b_nums[2]]

    hp, wp = mask_feat.shape[1], mask_feat.shape[2]
    x = mask_feat.reshape(in_ch, hp * wp)
    x = np.maximum(0.0, w0 @ x + b0[:, None])
    x = np.maximum(0.0, w1 @ x + b1[:, None])
    logits = (w2 @ x + b2[:, None]).reshape(hp, wp)
    return logits


def _refine_mask(refine_sess, src_pil, coarse_mask_u8, orig_w, orig_h, threshold):
    from PIL import Image

    inp_meta = refine_sess.get_inputs()[0]
    raw_h = inp_meta.shape[2] if len(inp_meta.shape) >= 4 else 1024
    refine_size = int(raw_h) if isinstance(raw_h, (int, np.integer)) and raw_h > 0 else 1024

    img_pad, (pt, pb, pl, pr), _ = _resize_pad(src_pil, refine_size, pad_value=(0, 0, 0))
    seg_pil = Image.fromarray(coarse_mask_u8, mode='L')
    seg_pad, _, _ = _resize_pad(seg_pil, refine_size, pad_value=0)

    img_np = np.asarray(img_pad, dtype=np.float32) / 255.0
    seg_np = np.asarray(seg_pad, dtype=np.float32) / 255.0
    inp = np.concatenate([img_np.transpose(2, 0, 1), seg_np[None, :, :]], axis=0)[None, :, :, :]

    out_names = [o.name for o in refine_sess.get_outputs()]
    outputs = refine_sess.run(out_names, {inp_meta.name: inp.astype(np.float32)})
    logits = np.asarray(outputs[0], dtype=np.float32)
    while logits.ndim > 2:
        logits = logits[0]

    prob = _sigmoid(logits)
    y_end = refine_size - pb if pb > 0 else refine_size
    x_end = refine_size - pr if pr > 0 else refine_size
    prob_crop = prob[pt:y_end, pl:x_end]
    mask_u8 = (prob_crop > threshold).astype(np.uint8) * 255
    mask_resized = Image.fromarray(mask_u8, mode='L').resize((orig_w, orig_h), Image.BILINEAR)
    return (np.asarray(mask_resized) >= 128).astype(np.uint8) * 255


def segment_instances(
    image_bytes,
    score_threshold=0.35,
    nms_threshold=0.45,
    mask_threshold=0.35,
    refine_threshold=0.35,
    max_instances=8,
    enable_refine=True,
):
    """Detect and segment all anime character instances in `image_bytes`.

    Returns list of `[{'box': [x1, y1, x2, y2], 'polygon': [[x, y], ...], 'score': float}, ...]`
    or None if the ONNX sessions are unavailable.
    """
    from PIL import Image
    from item import tagger

    sess_tuple = _load_sessions()
    if sess_tuple is None:
        return None
    seg_sess, refine_sess, _engine_name = sess_tuple

    src_pil = Image.open(io.BytesIO(image_bytes)).convert('RGB')
    orig_w, orig_h = src_pil.size

    inp_meta = seg_sess.get_inputs()[0]
    raw_h = inp_meta.shape[2] if len(inp_meta.shape) >= 4 else 640
    raw_w = inp_meta.shape[3] if len(inp_meta.shape) >= 4 else 640
    seg_h = int(raw_h) if isinstance(raw_h, (int, np.integer)) and raw_h > 0 else 640
    seg_w = int(raw_w) if isinstance(raw_w, (int, np.integer)) and raw_w > 0 else 640

    # Letterbox into (seg_w, seg_h) with gray (114, 114, 114) padding
    scale_ratio = min(float(seg_w) / float(max(1, orig_w)), float(seg_h) / float(max(1, orig_h)))
    new_w = max(1, int(round(orig_w * scale_ratio)))
    new_h = max(1, int(round(orig_h * scale_ratio)))
    resized = src_pil.resize((new_w, new_h), Image.Resampling.LANCZOS)
    pad_w = (seg_w - new_w) // 2
    pad_h = (seg_h - new_h) // 2
    canvas = Image.new('RGB', (seg_w, seg_h), (114, 114, 114))
    canvas.paste(resized, (pad_w, pad_h))

    arr_bgr = np.asarray(canvas)[:, :, ::-1].astype(np.float32)
    inp_tensor = ((arr_bgr - _RTMDET_MEAN) / _RTMDET_STD).transpose(2, 0, 1)[None, :, :, :]

    out_names = [o.name for o in seg_sess.get_outputs()]
    out_vals = seg_sess.run(out_names, {inp_meta.name: inp_tensor.astype(np.float32)})
    out = dict(zip(out_names, out_vals))

    if 'mask_proto' not in out:
        logger.warning('AnimeInstanceSegmentation output missing mask_proto: %s', out_names)
        return None

    candidates = []
    cand_boxes = []
    cand_scores = []
    strides = (8, 16, 32)
    for stride in strides:
        s_key = f'scores.stride{stride}'
        b_key = f'bboxes.stride{stride}'
        c_key = f'coeffs.stride{stride}'
        if s_key not in out or b_key not in out or c_key not in out:
            continue
        logits = np.asarray(out[s_key], dtype=np.float32)
        while logits.ndim > 2:
            logits = logits[0]
        probs = _sigmoid(logits)
        ys, xs = np.where(probs >= score_threshold)
        if ys.size == 0:
            continue
        bboxes_map = np.asarray(out[b_key], dtype=np.float32)[0]
        coeffs_map = np.asarray(out[c_key], dtype=np.float32)[0]
        for ry, cx in zip(ys.tolist(), xs.tolist()):
            sc = float(probs[ry, cx])
            x_anchor = float(cx * stride)
            y_anchor = float(ry * stride)
            l, t, r, b = bboxes_map[:, ry, cx]
            bx1 = x_anchor - float(l)
            by1 = y_anchor - float(t)
            bx2 = x_anchor + float(r)
            by2 = y_anchor + float(b)
            if bx2 <= bx1 or by2 <= by1:
                continue
            candidates.append((sc, stride, x_anchor, y_anchor, coeffs_map[:, ry, cx]))
            cand_boxes.append([bx1, by1, bx2, by2])
            cand_scores.append(sc)

    if not candidates:
        return []

    keep_indices = _nms_boxes(cand_boxes, cand_scores, iou_threshold=nms_threshold)
    keep_indices = keep_indices[:max_instances]

    proto = np.asarray(out['mask_proto'][0], dtype=np.float32)
    proto_ch, hp, wp = proto.shape
    yv, xv = np.meshgrid(
        np.arange(hp, dtype=np.float32),
        np.arange(wp, dtype=np.float32),
        indexing='ij',
    )

    regions = []
    for k_idx in keep_indices:
        sc, stride, x_anchor, y_anchor, kernel = candidates[k_idx]
        # Relative coordinates from each stride-8 prototype pixel to this instance's anchor
        rel_x = (x_anchor - xv * 8.0) / float(stride * 8.0)
        rel_y = (y_anchor - yv * 8.0) / float(stride * 8.0)
        mask_feat = np.concatenate([rel_x[None, :, :], rel_y[None, :, :], proto], axis=0)

        mask_logits = _apply_dynamic_conv(mask_feat, kernel, proto_ch)
        mask_prob = _sigmoid(mask_logits)
        mask_bin = (mask_prob > mask_threshold).astype(np.uint8) * 255

        mask_img = Image.fromarray(mask_bin, mode='L').resize((seg_w, seg_h), Image.BILINEAR)
        mask_crop = mask_img.crop((pad_w, pad_h, pad_w + new_w, pad_h + new_h))
        coarse_pil = mask_crop.resize((orig_w, orig_h), Image.BILINEAR)
        coarse_u8 = (np.asarray(coarse_pil) >= 128).astype(np.uint8) * 255

        if enable_refine and refine_sess is not None:
            try:
                final_u8 = _refine_mask(refine_sess, src_pil, coarse_u8, orig_w, orig_h, refine_threshold)
            except Exception:
                logger.debug('AnimeInstanceSegmentation refiner failed; using coarse mask', exc_info=True)
                final_u8 = coarse_u8
        else:
            final_u8 = coarse_u8

        poly, tight_box = tagger._mask_to_polygon(final_u8, offset_x=0, offset_y=0)
        if poly is not None and tight_box is not None:
            regions.append({'box': tight_box, 'polygon': poly, 'score': round(sc, 4)})

    return regions
