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


if __name__ == "__main__":
    unittest.main()
