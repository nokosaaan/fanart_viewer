import io
import os
import tempfile
import joblib
import numpy as np
from PIL import Image
from sklearn.linear_model import LogisticRegression
from rest_framework.test import APIClient
from django.test import TestCase
from item.models import Item, PreviewImage
from item import tagger
from security.token_utils import make_token


def _make_png(w=200, h=200):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (100, 150, 200)).save(buf, format="PNG")
    return buf.getvalue()


class BrokenRegionsAndOutliersTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        token = make_token("admin")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_broken_regions_detection_and_repair(self):
        item = Item.objects.create(
            external_id=101,
            source="twitter_bookmark",
            artist="Artist1",
            situation="CP",
            characters=["CharA", "CharB"],
            character_regions=[
                {"image_index": 0, "box": [300, 10, 500, 150], "characters": ["CharA"]},  # completely out of bounds
                {"image_index": 0, "box": [10, 10, 250, 150], "characters": ["CharB"]},   # partial out of bounds
            ],
        )
        PreviewImage.objects.create(item=item, order=0, data=_make_png(200, 200), content_type="image/png")

        r = self.client.get("/api/items/broken_regions/")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["count"], 1)
        codes = [iss["reason_code"] for iss in data["items"][0]["issues"]]
        self.assertIn("out_of_bounds_completely", codes)
        self.assertIn("out_of_bounds_partial", codes)

        r_fix = self.client.post(f"/api/items/{item.id}/fix_broken_regions/")
        self.assertEqual(r_fix.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(len(item.character_regions), 1)
        self.assertEqual(item.character_regions[0]["box"], [10, 10, 200, 150])
        self.assertEqual(item.characters, ["CharB"])

        r_after = self.client.get("/api/items/broken_regions/")
        self.assertEqual(r_after.json()["count"], 0)

    def test_label_outliers_and_apply_fix(self):
        # Create items: 3 clearly in CharA cluster, 3 clearly in CharB cluster, 1 mislabeled as CharA
        items_a = []
        for i in range(3):
            it = Item.objects.create(external_id=200 + i, source="pixiv_bookmark", artist="A", situation="SOLO", characters=["CharA"])
            items_a.append(it)
        items_b = []
        for i in range(3):
            it = Item.objects.create(external_id=300 + i, source="pixiv_bookmark", artist="B", situation="SOLO", characters=["CharB"])
            items_b.append(it)
        mislabeled = Item.objects.create(external_id=400, source="pixiv_bookmark", artist="A", situation="SOLO", characters=["CharA"])

        X_train = np.array([
            [1.0, 0.0], [0.95, 0.05], [0.9, 0.1],
            [0.0, 1.0], [0.05, 0.95], [0.1, 0.9],
        ], dtype=np.float32)
        y_train = np.array(["CharA", "CharA", "CharA", "CharB", "CharB", "CharB"])
        clf = LogisticRegression().fit(X_train, y_train)

        rows = [
            (items_a[0].id, "CharA", np.array([1.0, 0.0], dtype=np.float32), f"solo:{items_a[0].id}"),
            (items_a[1].id, "CharA", np.array([0.95, 0.05], dtype=np.float32), f"solo:{items_a[1].id}"),
            (items_a[2].id, "CharA", np.array([0.9, 0.1], dtype=np.float32), f"solo:{items_a[2].id}"),
            (items_b[0].id, "CharB", np.array([0.0, 1.0], dtype=np.float32), f"solo:{items_b[0].id}"),
            (items_b[1].id, "CharB", np.array([0.05, 0.95], dtype=np.float32), f"solo:{items_b[1].id}"),
            (items_b[2].id, "CharB", np.array([0.1, 0.9], dtype=np.float32), f"solo:{items_b[2].id}"),
            (mislabeled.id, "CharA", np.array([0.02, 0.98], dtype=np.float32), f"solo:{mislabeled.id}"),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            orig_data_dir = tagger._data_dir
            tagger._data_dir = lambda: tmpdir
            try:
                joblib.dump({
                    "classifier": clf,
                    "classes": list(clf.classes_),
                    "backend": "wd_v3",
                    "feature_source": "tags",
                    "feature_dim": 2,
                }, os.path.join(tmpdir, "character_classifier_wd_v3.joblib"))
                joblib.dump({
                    "rows": rows,
                    "manual_rows": [],
                }, os.path.join(tmpdir, "character_features_wd_v3.joblib"))

                r = self.client.get("/api/items/label_outliers/?min_prob=0.35")
                self.assertEqual(r.status_code, 200)
                data = r.json()
                self.assertEqual(data["outlier_count"], 1)
                self.assertEqual(data["outliers"][0]["item_id"], mislabeled.id)
                self.assertEqual(data["outliers"][0]["assigned_char"], "CharA")
                self.assertEqual(data["outliers"][0]["predicted_char"], "CharB")

                r_fix = self.client.post(f"/api/items/{mislabeled.id}/apply_label_fix/", {
                    "old_char": "CharA",
                    "new_char": "CharB",
                    "is_region": False,
                }, format="json")
                self.assertEqual(r_fix.status_code, 200)
                mislabeled.refresh_from_db()
                self.assertEqual(mislabeled.characters, ["CharB"])
            finally:
                tagger._data_dir = orig_data_dir

    def test_update_fields_removes_excluded_character_from_regions(self):
        item = Item.objects.create(
            external_id=501,
            source="pixiv_bookmark",
            artist="ArtistX",
            situation="CP",
            characters=["CharA", "CharB"],
            character_regions=[
                {"image_index": 0, "box": [10, 10, 100, 100], "characters": ["CharA"]},
                {"image_index": 0, "box": [110, 10, 200, 100], "characters": ["CharB"]},
            ],
        )

        # User removes CharB in EditFields and saves via update_fields
        r = self.client.post(f"/api/items/{item.id}/update_fields/", {
            "characters": ["CharA"],
        }, format="json")
        self.assertEqual(r.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.characters, ["CharA"])
        self.assertEqual(len(item.character_regions), 1)
        self.assertEqual(item.character_regions[0]["characters"], ["CharA"])

        # Subsequent sync_characters_to_regions must NOT resurrect CharB
        r_sync = self.client.post(f"/api/items/{item.id}/sync_characters_to_regions/")
        self.assertEqual(r_sync.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.characters, ["CharA"])

    def test_label_outliers_merges_training_feedback(self):
        import json
        import os
        import tempfile
        import joblib
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from item import tagger

        it_a = Item.objects.create(external_id=601, source="pixiv_bookmark", artist="A", situation="SOLO", characters=["CharA"])
        it_b = Item.objects.create(external_id=602, source="pixiv_bookmark", artist="B", situation="SOLO", characters=["CharB"])
        holdout_err = Item.objects.create(external_id=603, source="pixiv_bookmark", artist="C", situation="SOLO", characters=["CharA"])

        X_train = np.array([[1.0, 0.0], [0.95, 0.05], [0.0, 1.0], [0.05, 0.95]], dtype=np.float32)
        y_train = np.array(["CharA", "CharA", "CharB", "CharB"])
        clf = LogisticRegression().fit(X_train, y_train)

        rows = [
            (it_a.id, "CharA", np.array([1.0, 0.0], dtype=np.float32), f"item:{it_a.id}"),
            (it_b.id, "CharB", np.array([0.0, 1.0], dtype=np.float32), f"item:{it_b.id}"),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            orig_data_dir = tagger._data_dir
            tagger._data_dir = lambda: tmpdir
            try:
                joblib.dump({
                    "classifier": clf,
                    "classes": list(clf.classes_),
                    "backend": "wd_v3",
                    "feature_source": "tags",
                    "feature_dim": 2,
                }, os.path.join(tmpdir, "character_classifier_wd_v3.joblib"))
                joblib.dump({
                    "rows": rows,
                    "manual_rows": [],
                }, os.path.join(tmpdir, "character_features_wd_v3.joblib"))
                with open(os.path.join(tmpdir, "training_feedback.json"), "w", encoding="utf-8") as f:
                    json.dump({
                        "generated_at": "2026-11-17T00:00:00+00:00",
                        "feedback_threshold": 0.95,
                        "holdout_accuracy": 0.85,
                        "holdout_total": 10,
                        "low_accuracy_chars": [
                            {"character": "CharA", "accuracy": 0.8, "hits": 4, "support": 5}
                        ],
                        "misclassified": [
                            {
                                "item_id": holdout_err.id,
                                "key": f"item:{holdout_err.id}",
                                "is_region": False,
                                "assigned_char": "CharA",
                                "predicted_char": "CharB",
                                "predicted_prob": 0.82,
                                "assigned_prob": 0.18,
                                "char_accuracy": 0.8,
                                "char_support": 5,
                                "low_accuracy": True,
                            }
                        ],
                    }, f)

                r = self.client.get("/api/items/label_outliers/?min_prob=0.35")
                self.assertEqual(r.status_code, 200)
                data = r.json()
                self.assertIsNotNone(data["feedback_summary"])
                self.assertEqual(data["feedback_summary"]["feedback_threshold"], 0.95)
                self.assertEqual(data["feedback_summary"]["holdout_error_count"], 1)
                self.assertEqual(len(data["outliers"]), 1)
                o = data["outliers"][0]
                self.assertEqual(o["item_id"], holdout_err.id)
                self.assertTrue(o["from_holdout"])
                self.assertTrue(o["low_accuracy"])
                self.assertEqual(o["char_accuracy"], 0.8)

                # Once fixed on the item, it disappears from label_outliers
                self.client.post(f"/api/items/{holdout_err.id}/apply_label_fix/", {
                    "old_char": "CharA",
                    "new_char": "CharB",
                    "is_region": False,
                }, format="json")
                r2 = self.client.get("/api/items/label_outliers/?min_prob=0.35")
                self.assertEqual(r2.status_code, 200)
                self.assertEqual(len(r2.json()["outliers"]), 0)
            finally:
                tagger._data_dir = orig_data_dir

    def test_polygon_region_save_and_masked_crop(self):
        import io
        from PIL import Image
        from item import tagger

        item = Item.objects.create(
            external_id=701,
            source="pixiv_bookmark",
            artist="ArtistP",
            situation="CP",
        )
        poly = [[10, 10], [40, 10], [10, 40]]
        r = self.client.post(f"/api/items/{item.id}/character_regions/", {
            "regions": [
                {"image_index": 0, "box": [10, 10, 40, 40], "polygon": poly, "characters": ["CharA"]},
            ]
        }, format="json")
        self.assertEqual(r.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(len(item.character_regions), 1)
        self.assertEqual(item.character_regions[0]["polygon"], poly)

        # Create a 60x60 red image and crop with the upper-left triangle polygon
        src = Image.new("RGB", (60, 60), (200, 20, 20))
        buf = io.BytesIO()
        src.save(buf, format="PNG")
        cropped_bytes = tagger._crop_with_padding(buf.getvalue(), [10, 10, 40, 40], polygon=poly)
        out = Image.open(io.BytesIO(cropped_bytes)).convert("RGB")
        # Inside triangle (e.g. (5, 5) relative to x1=10, y1=10 -> (15, 15)) keeps red
        self.assertEqual(out.getpixel((5, 5)), (200, 20, 20))
        # Outside triangle inside bounding box (e.g. (25, 25) -> (35, 35)) is masked to white
        self.assertEqual(out.getpixel((25, 25)), (255, 255, 255))

    def test_extract_region_polygons_excludes_neighbor_head_and_preserves_in_proposed(self):
        import io
        from unittest.mock import patch
        import cv2
        import numpy as np
        from PIL import Image
        from item import tagger
        from item.views import _derive_proposed_regions

        src = Image.new("RGB", (200, 200), (240, 240, 240))
        buf = io.BytesIO()
        src.save(buf, format="PNG")
        img_bytes = buf.getvalue()

        # Two closely overlapping person boxes with heads where Head 1 sits inside Box 0's right edge
        boxes = [(10, 10, 130, 190), (70, 10, 190, 190)]
        head_boxes = [(30, 20, 70, 60), (100, 20, 140, 60)]

        # Mock get_isnetis_mask returning float [0, 1] foreground mask covering both characters
        fg = np.zeros((200, 200), dtype=np.float32)
        fg[15:185, 20:180] = 1.0
        with patch("imgutils.segment.isnetis.get_isnetis_mask", return_value=fg):
            regions = tagger._extract_region_polygons(img_bytes, boxes, head_boxes=head_boxes, use_isnetis=True, use_sam=False)

        self.assertEqual(len(regions), 2)
        poly0 = regions[0]["polygon"]
        poly1 = regions[1]["polygon"]
        self.assertIsNotNone(poly0)
        self.assertIsNotNone(poly1)
        self.assertGreaterEqual(len(poly0), 3)
        self.assertGreaterEqual(len(poly1), 3)

        cnt0 = np.array(poly0, dtype=np.int32)
        cnt1 = np.array(poly1, dtype=np.int32)
        # Head 0 center (50, 40) belongs to polygon 0 and NOT polygon 1
        self.assertGreaterEqual(cv2.pointPolygonTest(cnt0, (50, 40), False), 0)
        self.assertLess(cv2.pointPolygonTest(cnt1, (50, 40), False), 0)
        # Head 1 center (120, 40) belongs to polygon 1 and is excluded from polygon 0
        self.assertGreaterEqual(cv2.pointPolygonTest(cnt1, (120, 40), False), 0)
        self.assertLess(cv2.pointPolygonTest(cnt0, (120, 40), False), 0)

        # Verify _derive_proposed_regions preserves polygon on both multi-crop and single-region paths
        proposed_multi = _derive_proposed_regions(
            [(0, {
                "crops": [
                    {"box": regions[0]["box"], "polygon": poly0, "characters": [], "general_probs": None},
                    {"box": regions[1]["box"], "polygon": poly1, "characters": [], "general_probs": None},
                ],
                "boxes": [regions[0]["box"], regions[1]["box"]],
                "regions": regions,
            })],
            ["CharA", "CharB"],
            "onnx",
            image_indices=[0],
        )
        self.assertEqual(len(proposed_multi), 2)
        self.assertEqual(proposed_multi[0]["polygon"], poly0)
        self.assertEqual(proposed_multi[1]["polygon"], poly1)

        proposed_single = _derive_proposed_regions(
            [(0, {
                "crops": [],
                "boxes": [regions[0]["box"]],
                "regions": [regions[0]],
                "general_probs": None,
            })],
            ["CharA"],
            "onnx",
            image_indices=[0],
        )
        self.assertEqual(len(proposed_single), 1)
        self.assertEqual(proposed_single[0]["polygon"], poly0)

    def test_region_review_queue_filters(self):
        import io
        from PIL import Image
        from item.models import PreviewImage

        buf = io.BytesIO()
        Image.new("RGB", (100, 100), (200, 200, 200)).save(buf, format="PNG")
        img_bytes = buf.getvalue()

        # Item 1: polygon region inside bounds
        it_poly = Item.objects.create(
            external_id=801, source="pixiv_bookmark", artist="A", situation="CP",
            characters=["CharA"],
            character_regions=[{"image_index": 0, "box": [10, 10, 50, 50], "polygon": [[10, 10], [50, 10], [10, 50]], "characters": ["CharA"]}],
        )
        PreviewImage.objects.create(item=it_poly, order=0, data=img_bytes)

        # Item 2: rectangular box out of bounds (x2=180 > 100)
        it_oob = Item.objects.create(
            external_id=802, source="pixiv_bookmark", artist="B", situation="CP",
            characters=["CharB"],
            character_regions=[{"image_index": 0, "box": [20, 20, 180, 90], "characters": ["CharB"]}],
        )
        PreviewImage.objects.create(item=it_oob, order=0, data=img_bytes)

        r_all = self.client.get("/api/items/region_review_queue/?filter=all")
        self.assertEqual(r_all.status_code, 200)
        self.assertEqual({x["id"] for x in r_all.json()["results"]}, {it_poly.id, it_oob.id})

        r_box = self.client.get("/api/items/region_review_queue/?filter=box_only")
        self.assertEqual(r_box.status_code, 200)
        self.assertEqual([x["id"] for x in r_box.json()["results"]], [it_oob.id])

        r_iss = self.client.get("/api/items/region_review_queue/?filter=issues")
        self.assertEqual(r_iss.status_code, 200)
        self.assertEqual([x["id"] for x in r_iss.json()["results"]], [it_oob.id])
        self.assertGreater(len(r_iss.json()["results"][0]["region_issues"]), 0)

    def test_sam_segmenter_prompts_and_segment_point_endpoint(self):
        import io
        from unittest.mock import patch
        import numpy as np
        from PIL import Image
        from item import sam_segmenter
        from item.models import PreviewImage

        buf = io.BytesIO()
        Image.new("RGB", (200, 200), (220, 220, 220)).save(buf, format="PNG")
        img_bytes = buf.getvalue()

        captured_feeds = []

        class _DummyIO:
            def __init__(self, name, shape=None, typ="tensor(float)"):
                self.name = name
                self.shape = shape or [1, 3, 1024, 1024]
                self.type = typ

        class _FakeEncoder:
            def get_inputs(self):
                return [_DummyIO("image", [1, 3, 1024, 1024])]
            def get_outputs(self):
                return [
                    _DummyIO("image_embed", [1, 256, 64, 64]),
                    _DummyIO("high_res_feats_0", [1, 32, 256, 256]),
                    _DummyIO("high_res_feats_1", [1, 64, 128, 128]),
                ]
            def run(self, _out, _feed):
                return [
                    np.zeros((1, 256, 64, 64), dtype=np.float32),
                    np.zeros((1, 32, 256, 256), dtype=np.float32),
                    np.zeros((1, 64, 128, 128), dtype=np.float32),
                ]

        class _FakeDecoder:
            def get_inputs(self):
                return [
                    _DummyIO("image_embed"),
                    _DummyIO("high_res_feats_0"),
                    _DummyIO("high_res_feats_1"),
                    _DummyIO("point_coords"),
                    _DummyIO("point_labels", typ="tensor(int64)"),
                    _DummyIO("mask_input"),
                    _DummyIO("has_mask_input"),
                    _DummyIO("orig_im_size", typ="tensor(int32)"),
                ]
            def get_outputs(self):
                return [_DummyIO("masks"), _DummyIO("iou_predictions")]
            def run(self, _out, feed):
                captured_feeds.append(feed)
                # Return a clean square mask inside [20:90, 20:90] for mask index 1 (highest IoU)
                masks = np.full((1, 4, 256, 256), -5.0, dtype=np.float32)
                masks[0, 1, 25:115, 25:115] = 5.0
                ious = np.array([[0.3, 0.96, 0.5, 0.4]], dtype=np.float32)
                return [masks, ious]

        sam_segmenter._embed_cache.clear()
        with patch.object(sam_segmenter, "_load_sessions", return_value=(_FakeEncoder(), _FakeDecoder(), "sam2.1_small")):
            boxes = [(10, 10, 120, 190), (80, 10, 190, 190)]
            heads = [(30, 20, 70, 60), (110, 20, 150, 60)]
            regions = sam_segmenter.segment_boxes(img_bytes, boxes, head_boxes=heads)
            self.assertEqual(len(regions), 2)
            self.assertIsNotNone(regions[0]["polygon"])
            self.assertGreaterEqual(len(regions[0]["polygon"]), 3)
            # First box prompt must include box (2, 3), own head pos point (1), and neighbor head neg point (0)
            self.assertEqual(captured_feeds[0]["point_labels"].dtype, np.int64)
            self.assertEqual(list(captured_feeds[0]["point_labels"][0]), [2, 3, 1, 0])
            self.assertIn("high_res_feats_0", captured_feeds[0])
            self.assertIn("high_res_feats_1", captured_feeds[0])

            # Also verify POST /api/items/<id>/segment_point/ endpoint
            it = Item.objects.create(external_id=901, source="pixiv_bookmark", artist="S", situation="CP")
            PreviewImage.objects.create(item=it, order=0, data=img_bytes)
            r = self.client.post(f"/api/items/{it.id}/segment_point/", {
                "image_index": 0,
                "pos_points": [[50, 50]],
            }, format="json")
            self.assertEqual(r.status_code, 200)
            data = r.json()
            self.assertIn("polygon", data)
            self.assertGreaterEqual(len(data["polygon"]), 3)
