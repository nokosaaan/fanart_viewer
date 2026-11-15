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
                }, os.path.join(tmpdir, "character_classifier_cache_wd_v3_tags.joblib"))

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
