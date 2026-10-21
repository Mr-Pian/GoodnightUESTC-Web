"""Offline scheduler tests. No school client or network transport is imported."""
import argparse
import contextlib
import datetime as dt
import io
import tempfile
import unittest
from pathlib import Path

from scheduler_simulation import SimulationStore, check_schedule, coordinate_pair, main, parse_hhmm


class SchedulerSimulationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SimulationStore(Path(self.temp.name) / 'simulation.sqlite3')
        self.point = ('synthetic test point', 0.0, 0.0)
        self.schedule = parse_hhmm('21:30')

    def tearDown(self):
        self.temp.cleanup()

    def test_does_not_record_before_scheduled_time(self):
        now = dt.datetime(2026, 10, 21, 21, 29, 59)
        self.assertEqual(check_schedule(self.store, self.schedule, self.point, now), 'not_due')
        self.assertEqual(self.store.events(), [])

    def test_records_at_time_with_explicit_local_only_marker(self):
        now = dt.datetime(2026, 10, 21, 21, 30)
        self.assertEqual(check_schedule(self.store, self.schedule, self.point, now), 'recorded')
        event = self.store.events()[0]
        self.assertEqual(event['kind'], 'LOCAL_SIMULATION_ONLY')
        self.assertEqual(event['latitude'], 0.0)
        self.assertEqual(event['longitude'], 0.0)

    def test_at_most_one_record_per_local_day_across_store_instances(self):
        now = dt.datetime(2026, 10, 21, 21, 31)
        second_store = SimulationStore(Path(self.temp.name) / 'simulation.sqlite3')
        self.assertEqual(check_schedule(self.store, self.schedule, self.point, now), 'recorded')
        self.assertEqual(check_schedule(second_store, self.schedule, self.point, now), 'already_recorded')
        self.assertEqual(len(self.store.events()), 1)

    def test_next_day_can_record_again(self):
        first = dt.datetime(2026, 10, 21, 21, 31)
        next_day = dt.datetime(2026, 10, 22, 21, 31)
        self.assertEqual(check_schedule(self.store, self.schedule, self.point, first), 'recorded')
        self.assertEqual(check_schedule(self.store, self.schedule, self.point, next_day), 'recorded')
        self.assertEqual(len(self.store.events()), 2)

    def test_rejects_invalid_time_and_coordinates(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_hhmm('25:90')
        with self.assertRaises(argparse.ArgumentTypeError):
            coordinate_pair('nan', '104')
        with self.assertRaises(argparse.ArgumentTypeError):
            coordinate_pair('91', '104')

    def test_timezone_aware_clock_uses_its_local_wall_time(self):
        local = dt.timezone(dt.timedelta(hours=8))
        now = dt.datetime(2026, 10, 21, 21, 30, tzinfo=local)
        self.assertEqual(check_schedule(self.store, self.schedule, self.point, now), 'recorded')

    def test_cli_simulate_and_show_use_only_the_local_database(self):
        database = str(Path(self.temp.name) / 'cli.sqlite3')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(['--latitude', '0', '--longitude', '0', '--simulate-now',
                                   '--database', database]), 0)
            self.assertEqual(main(['--show', '--database', database]), 0)
        self.assertIn('不连接学校', output.getvalue())
        self.assertIn('LOCAL_SIMULATION_ONLY', output.getvalue())


if __name__ == '__main__':
    unittest.main()
