"""离线检查日期边界和返回范围，不需要账号或网络。"""
import importlib.util
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

spec = importlib.util.spec_from_file_location(
    "xiaomi_health", Path(__file__).resolve().parents[1] / "xiaomi_health.py")
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


class DailyQueryTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def timestamp(day):
        return int(datetime(2026, 1, day, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())

    async def test_window_filters_rows_and_uses_seconds(self):
        client = SimpleNamespace(_request=AsyncMock(return_value={"result": {
            "data_list": [
                {"time": self.timestamp(8), "value": '{"steps":100}'},
                {"time": self.timestamp(11), "value": '{"steps":500}'},
                {"time": self.timestamp(9), "value": {"steps":200}},
                {"time": self.timestamp(12), "value": '{"steps":300}'},
            ], "has_more": False}}))
        rows = await script.daily_records(client, 123, "steps", date(2026, 1, 11), 3)
        self.assertEqual([row["steps"] for row in rows], [200, 500])
        method, path = client._request.call_args.args
        self.assertEqual(method, "GET")
        self.assertEqual(path, "/app/v1/data/get_aggregated_fitness_data_by_time")
        params = client._request.call_args.kwargs["params"]
        self.assertEqual(params["start_time"], self.timestamp(9))
        self.assertEqual(params["end_time"], self.timestamp(12) - 1)
        self.assertEqual(params["tag"], "daily_report")

    async def test_incomplete_page_is_not_success(self):
        client = SimpleNamespace(_request=AsyncMock(return_value={"result": {
            "data_list": [], "has_more": True}}))
        with self.assertRaisesRegex(ValueError, "分页"):
            await script.daily_records(client, 123, "steps", date(2026, 1, 11), 1)

    async def test_future_window_does_not_send_request(self):
        client = SimpleNamespace(_request=AsyncMock())
        rows = await script.daily_records(client, 123, "steps", date(2099, 1, 1), 1)
        self.assertEqual(rows, [])
        client._request.assert_not_awaited()


class WeightQueryTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def timestamp(day, hour=0):
        return int(datetime(2026, 1, day, hour, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())

    @staticmethod
    def client(*pages):
        return SimpleNamespace(_request=AsyncMock(
            side_effect=[{"result": page} for page in pages]))

    async def test_pagination_and_raw_fields(self):
        client = self.client(
            {"data_list": [
                {"time": self.timestamp(9), "zone_offset": 28800,
                 "value": '{"weight":72.5,"bmi":23.0,"body_fat_rate":22.1}'},
                {"time": self.timestamp(10, 12), "zone_offset": 28800,
                 "value": {"weight": 72.1, "bmi": 22.9}},
            ], "has_more": True, "next_key": "cursor-1"},
            {"data_list": [
                {"time": self.timestamp(10, 18), "value": '{"weight":72.0,"muscle_rate":42.0}'},
                {"time": self.timestamp(11), "value": '{"weight":72.2,"bmi":22.9}'},
            ], "has_more": False},
        )
        rows = await script.fitness_records(client, "weight", date(2026, 1, 11), 3)
        self.assertEqual([row["weight"] for row in rows], [72.5, 72.1, 72.0, 72.2])
        first, second = client._request.call_args_list
        self.assertEqual(first.args, ("POST", "/app/v1/data/get_fitness_data_by_time"))
        self.assertEqual(first.kwargs["params"]["key"], "weight")
        self.assertNotIn("next_key", first.kwargs["params"])
        self.assertNotIn("tag", first.kwargs["params"])
        self.assertEqual(second.kwargs["params"]["next_key"], "cursor-1")
        self.assertEqual(rows[0]["body_fat_rate"], 22.1)

    async def test_invalid_weights_are_skipped(self):
        client = self.client({"data_list": [
            {"time": self.timestamp(9), "value": '{"weight":0,"bmi":20}'},
            {"time": self.timestamp(9, 6), "value": '{"weight":-1}'},
            {"time": self.timestamp(9, 9), "value": '{"bmi":21}'},
            {"time": self.timestamp(9, 12), "value": 'not-json'},
            {"time": "bad", "value": '{"weight":70}'},
            {"time": self.timestamp(9, 18), "value": '{"weight":71,"bmi":0}'},
        ], "has_more": False})
        rows = await script.fitness_records(client, "weight", date(2026, 1, 11), 3)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["weight"], 71)
        self.assertIsNone(script._optional_number(rows[0]["bmi"]))

    async def test_duplicate_timestamp_keeps_last(self):
        client = self.client({"data_list": [
            {"time": self.timestamp(10), "value": '{"weight":70}'},
            {"time": self.timestamp(10), "value": '{"weight":70.5}'},
        ], "has_more": False})
        rows = await script.fitness_records(client, "weight", date(2026, 1, 10), 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["weight"], 70.5)

    async def test_window_filters_outside_records(self):
        client = self.client({"data_list": [
            {"time": self.timestamp(8), "value": '{"weight":70}'},
            {"time": self.timestamp(10), "value": '{"weight":72}'},
            {"time": self.timestamp(12), "value": '{"weight":71}'},
        ], "has_more": False})
        rows = await script.fitness_records(client, "weight", date(2026, 1, 11), 3)
        self.assertEqual([row["weight"] for row in rows], [72])

    async def test_future_window_does_not_send_request(self):
        client = SimpleNamespace(_request=AsyncMock())
        rows = await script.fitness_records(client, "weight", date(2099, 1, 1), 1)
        self.assertEqual(rows, [])
        client._request.assert_not_awaited()

    async def test_missing_cursor_after_has_more(self):
        client = self.client({"data_list": [], "has_more": True})
        with self.assertRaisesRegex(ValueError, "分页"):
            await script.fitness_records(client, "weight", date(2026, 1, 11), 1)

    async def test_repeated_cursor(self):
        client = self.client(
            {"data_list": [], "has_more": True, "next_key": "same"},
            {"data_list": [], "has_more": True, "next_key": "same"},
        )
        with self.assertRaisesRegex(ValueError, "游标"):
            await script.fitness_records(client, "weight", date(2026, 1, 11), 1)

    async def test_page_cap_stops_pagination(self):
        client = self.client(
            {"data_list": [], "has_more": True, "next_key": "a"},
            {"data_list": [], "has_more": True, "next_key": "b"},
        )
        with self.assertRaisesRegex(ValueError, "分页"):
            await script.fitness_records(client, "weight", date(2026, 1, 11), 1, max_pages=2)

    def test_weight_daily_keeps_last_measurement_per_day(self):
        rows = [
            {"time": self.timestamp(9, 8), "weight": 70},
            {"time": self.timestamp(9, 20), "weight": 71},
            {"time": self.timestamp(10, 7), "weight": 71.5},
        ]
        daily = script.weight_daily(rows)
        self.assertEqual([row["weight"] for row in daily], [71, 71.5])
        self.assertEqual([row["time"] for row in daily],
                         [self.timestamp(9, 20), self.timestamp(10, 7)])

    def test_weight_text_lists_only_present_fields(self):
        row = {"weight": 72.5, "bmi": 23.0, "body_fat_rate": 22.1,
               "muscle_rate": 40.0, "body_age": 0}
        self.assertEqual(
            script.weight_text(row),
            "72.5 千克，BMI 23.0，体脂 22.1%，肌肉量 29.0 千克")
        self.assertEqual(script.weight_text({"weight": 72.5}),
                         "72.5 千克，BMI 未测量")

    def test_weight_text_prefers_upstream_muscle_mass(self):
        row = {"weight": 67.0, "bmi": 23.4, "muscle_mass": 51.4,
               "muscle_rate": 76.7, "skeletal_muscle_mass": 29.2,
               "fat_mass": 13.5}
        text = script.weight_text(row)
        self.assertIn("肌肉量 51.4 千克", text)
        self.assertIn("骨骼肌量 29.2 千克", text)
        self.assertIn("体脂量 13.5 千克", text)


if __name__ == "__main__":
    unittest.main()
