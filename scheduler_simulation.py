"""Offline scheduler prototype. This module never contacts school services."""
import argparse
import datetime as dt
import math
import sqlite3
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / 'private-data' / 'simulation.sqlite3'


def parse_hhmm(value):
    if (len(value) != 5 or value[2] != ':' or
            not (value[:2] + value[3:]).isdigit()):
        raise argparse.ArgumentTypeError('时间格式应为 HH:MM，例如 21:30。')
    try:
        return dt.time(int(value[:2]), int(value[3:]))
    except ValueError as error:
        raise argparse.ArgumentTypeError('时间格式应为 HH:MM，例如 21:30。') from error


def coordinate_pair(latitude, longitude):
    try:
        lat, lon = float(latitude), float(longitude)
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError('经纬度必须是数字。') from error
    if not math.isfinite(lat) or not math.isfinite(lon) or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        raise argparse.ArgumentTypeError('纬度范围为 -90 到 90，经度范围为 -180 到 180。')
    return lat, lon


class SimulationStore:
    """Persist local-only simulation events, at most one per local calendar day."""

    def __init__(self, database):
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database) as connection:
            connection.execute('''
                CREATE TABLE IF NOT EXISTS simulation_events (
                    local_date TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    label TEXT NOT NULL,
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    kind TEXT NOT NULL CHECK (kind = 'LOCAL_SIMULATION_ONLY')
                )
            ''')

    def record_once(self, now, label, latitude, longitude):
        local_date = now.date().isoformat()
        with sqlite3.connect(self.database, timeout=30) as connection:
            cursor = connection.execute(
                '''INSERT OR IGNORE INTO simulation_events
                   (local_date, created_at, label, latitude, longitude, kind)
                   VALUES (?, ?, ?, ?, ?, 'LOCAL_SIMULATION_ONLY')''',
                (local_date, now.isoformat(timespec='seconds'), label, latitude, longitude),
            )
            return cursor.rowcount == 1

    def events(self):
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(
                'SELECT * FROM simulation_events ORDER BY local_date DESC')]


def check_schedule(store, scheduled_time, point, now):
    """Record a local simulation once the configured time is reached."""
    local_time = now.time().replace(tzinfo=None, second=0, microsecond=0)
    if local_time < scheduled_time:
        return 'not_due'
    label, latitude, longitude = point
    if store.record_once(now, label, latitude, longitude):
        return 'recorded'
    return 'already_recorded'


def _arguments(argv=None):
    parser = argparse.ArgumentParser(
        description='离线定时模拟器：只在本机写入模拟记录，不会连接学校或提交签到。')
    parser.add_argument('--daily-at', type=parse_hhmm, default=dt.time(21, 30), metavar='HH:MM',
                        help='每天按本机时区触发的时间，默认 21:30。')
    parser.add_argument('--latitude', help='固定测试点纬度。')
    parser.add_argument('--longitude', help='固定测试点经度。')
    parser.add_argument('--label', default='本地模拟位置', help='本地记录中的测试点名称。')
    parser.add_argument('--database', type=Path, default=DEFAULT_DB,
                        help='本地 SQLite 文件，默认 private-data/simulation.sqlite3。')
    parser.add_argument('--simulate-now', action='store_true',
                        help='立即写一条本地模拟记录，用于快速测试，不访问学校。')
    parser.add_argument('--check-once', action='store_true',
                        help='只检查当前时间是否到点，然后退出。')
    parser.add_argument('--show', action='store_true', help='显示本机保存的模拟记录，然后退出。')
    args = parser.parse_args(argv)
    if args.show:
        return args, None
    if args.latitude is None or args.longitude is None:
        parser.error('运行模拟器时必须同时提供 --latitude 和 --longitude。')
    try:
        latitude, longitude = coordinate_pair(args.latitude, args.longitude)
    except argparse.ArgumentTypeError as error:
        parser.error(str(error))
    args.point = (args.label, latitude, longitude)
    return args, parser


def main(argv=None):
    args, _ = _arguments(argv)
    store = SimulationStore(args.database)
    if args.show:
        for event in store.events():
            print(f"{event['local_date']} {event['created_at']} | {event['label']} "
                  f"({event['latitude']}, {event['longitude']}) | {event['kind']}")
        return 0

    print('离线模拟模式：只写本地模拟记录，不连接学校，不会产生学校签到记录。')
    if args.simulate_now:
        status = 'recorded' if store.record_once(dt.datetime.now().astimezone(), *args.point) else 'already_recorded'
        print('已写入本地模拟记录。' if status == 'recorded' else '今天已有本地模拟记录，未重复写入。')
        return 0

    if args.check_once:
        status = check_schedule(store, args.daily_at, args.point, dt.datetime.now().astimezone())
        print({'not_due': '尚未到设定时间，没有写入记录。',
               'recorded': '已写入本地模拟记录。',
               'already_recorded': '今天已有本地模拟记录，未重复写入。'}[status])
        return 0

    print(f'每日触发时间：{args.daily_at:%H:%M}（本机时区）；按 Ctrl+C 停止。')
    try:
        while True:
            status = check_schedule(store, args.daily_at, args.point, dt.datetime.now().astimezone())
            if status == 'recorded':
                print(f"{dt.datetime.now().astimezone().isoformat(timespec='seconds')} 已写入本地模拟记录。")
            time.sleep(10)
    except KeyboardInterrupt:
        print('\n模拟器已停止。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
