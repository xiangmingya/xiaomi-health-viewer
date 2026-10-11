#!/usr/bin/env python3
"""小米运动健康：扫码登录、查看云端最新数据和历史摘要。Python 3.11+。"""
import argparse
import asyncio
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("login", "latest", "history"))
    parser.add_argument("--token", type=Path, default=Path(os.environ.get(
        "MI_TOKEN_FILE", str(Path(__file__).resolve().parent / "private" / "token.json"))))
    parser.add_argument("--date", type=date.fromisoformat,
                        default=datetime.now(ZoneInfo("Asia/Shanghai")).date(),
                        help="历史查询结束日期，YYYY-MM-DD，默认北京时间今天")
    parser.add_argument("--days", type=int, default=1, help="包含结束日期的查询天数")
    parser.add_argument("--probe", action="store_true", help="扫码登录后立即试读最新数据")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON，默认显示中文摘要")
    args = parser.parse_args()
    if not 1 <= args.days <= 31:
        parser.error("--days 取 1 至 31；较长历史请分段查询")
    return args


async def daily_records(client, uid, key, end_date, days):
    """本人日报实测使用秒级 snake_case 参数；额外在本地校验日期范围。"""
    tz = ZoneInfo("Asia/Shanghai")
    start = datetime.combine(end_date - timedelta(days=days - 1), datetime.min.time(), tz)
    end = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tz) - timedelta(seconds=1)
    end = min(end, datetime.now(tz))
    if end < start:
        return []
    lower, upper = int(start.timestamp()), int(end.timestamp())
    params = {"relative_uid": uid, "key": key, "tag": "daily_report",
              "start_time": lower, "end_time": upper, "limit": max(days, 7)}
    result = await client._request("GET", "/app/v1/data/get_aggregated_fitness_data_by_time", params=params)
    response = result.get("result") or {}
    rows = []
    for item in response.get("data_list") or []:
        timestamp = item.get("time", 0)
        if lower <= timestamp <= upper:
            value = item.get("value") or {}
            if isinstance(value, str):
                value = json.loads(value)
            if not isinstance(value, dict):
                raise ValueError("日报内容格式异常")
            rows.append({**value, "time": timestamp})
    if response.get("has_more"):
        raise ValueError("日报分页未读完，请缩小查询范围")
    return sorted(rows, key=lambda x: x["time"])


def format_time(timestamp, *, day=False):
    if not timestamp:
        return "日期未提供"
    return datetime.fromtimestamp(timestamp, ZoneInfo("Asia/Shanghai")).strftime(
        "%Y-%m-%d" if day else "%Y-%m-%d %H:%M")


def print_report(result, as_json=False):
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return
    print(f"查询时间：{result['queried_at']}")
    print("数据来自云端最近日报，手机 App 同步后再刷新可能更新。")
    labels = {"steps": "步数", "heart_rate": "心率", "sleep": "睡眠", "spo2": "血氧"}
    for key, label in labels.items():
        value = result.get(key)
        if isinstance(value, dict) and "error" in value:
            detail = value.get("detail") or value["error"]
            print(f"{label}：请求失败（{detail}）")
            continue
        rows = value if isinstance(value, list) else ([value] if value else [])
        if not rows:
            print(f"{label}：查询范围内未返回数据")
        for row in rows:
            if key == "steps":
                text = f"{row.get('steps')} 步，距离 {row.get('distance')} 米，热量 {row.get('calories')} 千卡"
            elif key == "heart_rate":
                text = f"平均 {row.get('avg_hr')}，静息 {row.get('avg_rhr')}，最低 {row.get('min_hr')}，最高 {row.get('max_hr')} 次/分"
                sample = row.get("latest_hr")
                if sample:
                    text += f"；最近采样 {sample.get('bpm')} 次/分（{format_time(sample.get('time'))}）"
            elif key == "sleep":
                total = row.get('total_duration', 0)
                text = f"{total // 60} 小时 {total % 60} 分钟，评分 {row.get('sleep_score')}"
            else:
                text = f"平均 {row.get('avg_spo2')}%，最低 {row.get('min_spo2')}%，最高 {row.get('max_spo2')}%"
                sample = row.get("latest_spo2")
                if sample:
                    text += f"；最近采样 {sample.get('spo2')}%（{format_time(sample.get('time'))}）"
            print(f"{label} [{format_time(row.get('time'), day=True)}]：{text}")
    print(flush=True)


def show_error(exc):
    # 不输出 SDK 的响应体或请求 URL，避免登录凭据进入日志。
    from mi_fitness import FamilyMemberNotFoundError, TokenExpiredError
    if isinstance(exc, TokenExpiredError):
        print("登录凭据自动刷新失败，请重新运行 login 扫码。", flush=True)
    elif isinstance(exc, FamilyMemberNotFoundError):
        print("本人查询返回亲友关系错误，请核对接口和账号。", flush=True)
    else:
        status = getattr(exc, "status_code", None)
        print(f"请求失败：{type(exc).__name__}"
              + (f"（状态码 {status}）" if status is not None else ""), flush=True)


async def query(args):
    from mi_fitness import MiHealthClient
    if not args.token.is_file():
        print("尚未登录，请先运行 login。", flush=True)
        return 1
    args.token.chmod(0o600)
    async with MiHealthClient.from_token(args.token) as client:
        try:
            uid = int(client.auth.token.user_id)
            result = {
                "queried_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                "source": "小米运动健康云端，更新取决于手机 App 同步",
            }
            latest = args.command == "latest" or args.probe
            days = 7 if latest else args.days
            result.update(mode="latest_daily" if latest else "history",
                          end_date=args.date.isoformat(), days=days)
            failed = False
            # 本人的 latest 接口实测返回 -8；改从最近日报中取各指标最新一条。
            for name in ("steps", "heart_rate", "sleep", "spo2"):
                try:
                    items = await asyncio.wait_for(daily_records(client, uid, name, args.date, days), timeout=45)
                    result[name] = (items[-1] if items else None) if latest else items
                except Exception as exc:
                    failed = True
                    result[name] = {"error": type(exc).__name__,
                                    "status_code": getattr(exc, "status_code", None),
                                    "code": getattr(exc, "code", None)}
                    if isinstance(exc, ValueError) and str(exc) in (
                        "日报内容格式异常", "日报分页未读完，请缩小查询范围"
                    ):
                        result[name]["detail"] = str(exc)
            print_report(result, args.json)
            return 1 if failed else 0
        finally:
            await client.auth.close()


async def main(args):
    from loguru import logger
    from mi_fitness import XiaomiAuth
    logger.remove()
    if args.command == "login":
        args.token.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

        async def on_qr(qr_image_url, login_url):
            print("请扫码并在手机上确认登录。二维码最长等待约 5 分钟。", flush=True)
            print(f"二维码图片：{qr_image_url}", flush=True)
            if login_url:
                print(f"备用登录链接：{login_url}", flush=True)

        async with XiaomiAuth() as auth:
            await auth.login_qr(qr_callback=on_qr, max_wait=300)
            auth.save_token(args.token)
            args.token.chmod(0o600)
        print("登录成功，凭据已保存到本地私有文件。", flush=True)
        if not args.probe:
            return 0
    return await query(args)


if __name__ == "__main__":
    os.umask(0o077)
    # 保留用户代理，仅在本进程中去掉 httpx 不兼容的 IPv6 方括号。
    for env_name in ("NO_PROXY", "no_proxy"):
        if env_name in os.environ:
            os.environ[env_name] = os.environ[env_name].replace("[::1]", "::1")
    args = arguments()
    try:
        raise SystemExit(asyncio.run(main(args)))
    except KeyboardInterrupt:
        print("已取消。")
        raise SystemExit(130)
    except ModuleNotFoundError:
        print("缺少依赖，请在 Python 3.11+ 环境安装：python -m pip install mi-fitness==0.2.0")
        raise SystemExit(1)
    except Exception as exc:
        show_error(exc)
        raise SystemExit(1)
