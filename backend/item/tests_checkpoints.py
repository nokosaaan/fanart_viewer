from django.test import TestCase
from rest_framework.test import APIClient
from item.models import Item
from security.token_utils import make_token


class CheckpointsTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        token = make_token('admin')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        self.item = Item.objects.create(
            external_id=999001,
            artist="Sample Artist",
            titles=["Sample Manga Anthology"],
            link="https://example.com/item/1",
            checkpoints=[
                {"page_index": 0, "label": "第1話"},
                {"page_index": 15, "label": "第2話"},
            ],
        )

    def test_get_checkpoints(self):
        res = self.client.get(f"/api/items/{self.item.id}/checkpoints/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("checkpoints", data)
        self.assertEqual(len(data["checkpoints"]), 2)
        self.assertEqual(data["checkpoints"][0]["label"], "第1話")
        self.assertEqual(data["checkpoints"][1]["page_index"], 15)

    def test_update_checkpoints_action(self):
        new_cps = [
            {"page_index": 30, "label": "第3話"},
            {"page_index": 0, "label": "プロローグ"},
            {"page_index": 12, "label": ""},  # empty label gets auto fallback
        ]
        res = self.client.post(
            f"/api/items/{self.item.id}/checkpoints/",
            {"checkpoints": new_cps},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "saved")
        # Should be sorted by page_index
        cps = data["checkpoints"]
        self.assertEqual(len(cps), 3)
        self.assertEqual(cps[0]["page_index"], 0)
        self.assertEqual(cps[0]["label"], "プロローグ")
        self.assertEqual(cps[1]["page_index"], 12)
        self.assertEqual(cps[1]["label"], "第2話")
        self.assertEqual(cps[2]["page_index"], 30)
        self.assertEqual(cps[2]["label"], "第3話")

        self.item.refresh_from_db()
        self.assertEqual(self.item.checkpoints, cps)

    def test_update_checkpoints_via_update_fields(self):
        new_cps = [
            {"page_index": 5, "label": "第1話"},
        ]
        res = self.client.post(
            f"/api/items/{self.item.id}/update_fields/",
            {"checkpoints": new_cps},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.item.refresh_from_db()
        self.assertEqual(len(self.item.checkpoints), 1)
        self.assertEqual(self.item.checkpoints[0]["page_index"], 5)
