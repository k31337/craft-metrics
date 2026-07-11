"""Unit tests for parsing.py — pure functions only, no live server required."""

import json
import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parsing import (
    build_line,
    compute_session_stats,
    parse_log_lines,
    parse_stats,
    session_stats_to_line,
    session_to_line,
)

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def load_fixture_json(name):
    with open(os.path.join(FIXTURES_DIR, name)) as f:
        return json.load(f)


def load_fixture_lines(name):
    with open(os.path.join(FIXTURES_DIR, name)) as f:
        return f.readlines()


class BuildLineTests(unittest.TestCase):
    def test_int_field_gets_i_suffix(self):
        line = build_line("m", {"player": "Steve"}, {"count": 5}, 100)
        self.assertEqual(line, "m,player=Steve count=5i 100")

    def test_float_field_has_no_suffix(self):
        line = build_line("m", {}, {"rate": 1.5}, 100)
        self.assertEqual(line, "m rate=1.5 100")

    def test_tag_value_is_escaped(self):
        line = build_line("m", {"player": "a b,c=d"}, {"count": 1}, 100)
        self.assertEqual(line, "m,player=a_b_c_d count=1i 100")


class ParseStatsTests(unittest.TestCase):
    def setUp(self):
        self.stats_json = load_fixture_json("sample_stats.json")

    def test_summary_line_has_expected_totals(self):
        lines, _ = parse_stats(self.stats_json, "Steve", None, 1_000_000_000)
        summary = next(l for l in lines if l.startswith("mc_stats_summary"))
        self.assertIn("blocks_mined_total=321i", summary)  # 245 + 64 + 12
        self.assertIn("mobs_killed_total=27i", summary)  # 18 + 9
        self.assertIn("playtime_hours=2.0", summary)  # 144000 ticks / 20 / 3600
        self.assertIn("distance_walk_m=12500.0", summary)  # 1250000cm / 100

    def test_block_and_mob_breakdown(self):
        lines, _ = parse_stats(self.stats_json, "Steve", None, 1_000_000_000)
        self.assertTrue(any("mc_stats_block,player=Steve,block=stone count=245i" in l for l in lines))
        self.assertTrue(any("mc_stats_mob,player=Steve,mob=zombie count=18i" in l for l in lines))

    def test_station_automation_and_other_breakdown(self):
        lines, _ = parse_stats(self.stats_json, "Steve", None, 1_000_000_000)
        self.assertTrue(any("mc_stats_station,player=Steve,station=furnace count=22i" in l for l in lines))
        self.assertTrue(any("mc_stats_automation,player=Steve,action=inspect_hopper count=6i" in l for l in lines))
        self.assertTrue(any("mc_stats_other,player=Steve,action=traded_with_villager count=4i" in l for l in lines))

    def test_no_derived_line_without_previous_snapshot(self):
        lines, _ = parse_stats(self.stats_json, "Steve", None, 1_000_000_000)
        self.assertFalse(any(l.startswith("mc_stats_derived") for l in lines))

    def test_derived_rate_uses_delta_not_cumulative_total(self):
        _, first_snapshot = parse_stats(self.stats_json, "Steve", None, 1_000_000_000)

        second_stats = json.loads(json.dumps(self.stats_json))
        second_stats["stats"]["minecraft:mined"]["minecraft:stone"] += 60  # +60 blocks mined
        second_stats["stats"]["minecraft:custom"]["minecraft:play_time"] += 20 * 3600  # +1 hour played

        lines, _ = parse_stats(second_stats, "Steve", first_snapshot, 2_000_000_000)
        derived = next(l for l in lines if l.startswith("mc_stats_derived"))
        self.assertIn("blocks_mined_per_hour=60.0", derived)

    def test_missing_stats_default_to_zero_without_crashing(self):
        lines, _ = parse_stats({"stats": {}}, "Steve", None, 1_000_000_000)
        summary = next(l for l in lines if l.startswith("mc_stats_summary"))
        self.assertIn("blocks_mined_total=0i", summary)


class ParseLogLinesTests(unittest.TestCase):
    def test_join_then_leave_across_two_cycles_produces_one_session(self):
        all_lines = load_fixture_lines("sample_latest.log")
        leave_index = next(i for i, l in enumerate(all_lines) if "left the game" in l)

        now_join = datetime(2026, 7, 11, 9, 15, 3, tzinfo=timezone.utc)
        now_leave = datetime(2026, 7, 11, 10, 42, 51, tzinfo=timezone.utc)
        open_sessions = {}

        completed_first_cycle = parse_log_lines(all_lines[:leave_index], now_join, open_sessions)
        self.assertEqual(completed_first_cycle, [])
        self.assertIn("Steve", open_sessions)

        completed_second_cycle = parse_log_lines(all_lines[leave_index:], now_leave, open_sessions)
        self.assertEqual(len(completed_second_cycle), 1)
        session = completed_second_cycle[0]
        self.assertEqual(session["player"], "Steve")
        self.assertEqual(session["duration_seconds"], (now_leave - now_join).total_seconds())
        self.assertEqual(open_sessions, {})

    def test_leave_without_prior_join_is_ignored(self):
        lines = ["[10:00:00] [Server thread/INFO]: Alex left the game"]
        completed = parse_log_lines(lines, datetime(2026, 7, 11, tzinfo=timezone.utc), {})
        self.assertEqual(completed, [])

    def test_unrelated_log_lines_are_ignored(self):
        lines = ["[09:00:12] [Server thread/INFO]: Starting minecraft server version 1.21.1"]
        open_sessions = {}
        completed = parse_log_lines(lines, datetime(2026, 7, 11, tzinfo=timezone.utc), open_sessions)
        self.assertEqual(completed, [])
        self.assertEqual(open_sessions, {})


class ComputeSessionStatsTests(unittest.TestCase):
    def test_streak_broken_after_a_gap_day(self):
        now = datetime(2026, 7, 11, tzinfo=timezone.utc)
        sessions = [
            {"join": datetime(2026, 7, 11, tzinfo=timezone.utc), "duration_seconds": 100},
            {"join": datetime(2026, 7, 10, tzinfo=timezone.utc), "duration_seconds": 200},
            {"join": datetime(2026, 7, 8, tzinfo=timezone.utc), "duration_seconds": 300},  # gap on the 9th
        ]
        stats = compute_session_stats(sessions, datetime(2026, 7, 1, tzinfo=timezone.utc), now)
        self.assertEqual(stats["current_streak_days"], 2)
        self.assertEqual(stats["longest_session_seconds"], 300)
        self.assertEqual(stats["total_sessions"], 3)

    def test_streak_is_zero_when_last_play_was_two_days_ago(self):
        now = datetime(2026, 7, 11, tzinfo=timezone.utc)
        sessions = [{"join": datetime(2026, 7, 9, tzinfo=timezone.utc), "duration_seconds": 100}]
        stats = compute_session_stats(sessions, datetime(2026, 7, 1, tzinfo=timezone.utc), now)
        self.assertEqual(stats["current_streak_days"], 0)

    def test_open_session_today_counts_toward_streak(self):
        now = datetime(2026, 7, 11, tzinfo=timezone.utc)
        sessions = [{"join": datetime(2026, 7, 10, tzinfo=timezone.utc), "duration_seconds": 100}]
        stats = compute_session_stats(
            sessions, datetime(2026, 7, 1, tzinfo=timezone.utc), now, extra_played_dates={now.date()}
        )
        self.assertEqual(stats["current_streak_days"], 2)


class SessionLineTests(unittest.TestCase):
    def test_session_to_line_and_stats_to_line(self):
        session = {
            "player": "Steve",
            "join": datetime(2026, 7, 11, 9, 0, 0, tzinfo=timezone.utc),
            "leave": datetime(2026, 7, 11, 10, 30, 0, tzinfo=timezone.utc),
            "duration_seconds": 5400,
        }
        line = session_to_line(session, 1_000_000_000)
        self.assertIn("mc_session,player=Steve", line)
        self.assertIn("duration_seconds=5400i", line)

        stats_line = session_stats_to_line(
            {"server_age_days": 5, "current_streak_days": 2, "longest_session_seconds": 5400, "total_sessions": 3},
            2_000_000_000,
        )
        self.assertTrue(stats_line.startswith("mc_session_stats "))
        self.assertIn("current_streak_days=2i", stats_line)


if __name__ == "__main__":
    unittest.main()
