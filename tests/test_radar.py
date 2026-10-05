"""Radar: labels, ages, day boundaries, and the lane filters.

Uses a temp DB rather than the real log. Same two bootstrap lines as
test_grammar.py: sys.path so the flat modules import, and a dummy vault so
paths.py's import-time sys.exit never fires (radar.py does not import paths,
but __main__ and its siblings do).
"""
import os
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "rhythm"))
os.environ.setdefault("RHYTHM_VAULT", "/nonexistent-vault-for-tests")

import radar  # noqa: E402


class Label(unittest.TestCase):
    """A curated window name leads; a shell's own name yields to the folder."""

    def test_curated_name_wins(self):
        self.assertEqual(radar._label("Home", "nix-os", "/home/p/nixos-config", False),
                         "Home / nix-os")

    def test_default_name_falls_back_to_folder(self):
        # @3 and @4 are both called "bash"; the folder is what tells them apart
        self.assertEqual(radar._label("Home", "bash", "/home/p/vaults/root-engine/kanban", False),
                         "Home / kanban")
        self.assertEqual(radar._label("Home", "bash", "/home/p/vaults/root-engine", False),
                         "Home / root-engine")

    def test_claude_marks_the_whole_window(self):
        # window-wide, so focusing the shell pane of a Claude window does
        # not silently drop the marker
        self.assertEqual(radar._label("Home", "tools", "/home/p/dev/x", True),
                         "Home / tools · Claude")

    def test_no_claude_no_marker(self):
        self.assertEqual(radar._label("Home", "tools", "/home/p/dev/x", False),
                         "Home / tools")

    def test_no_name_no_folder(self):
        self.assertEqual(radar._label("Home", "", "", False), "Home / shell")


class Age(unittest.TestCase):
    NOW = int(time.mktime((2026, 9, 23, 14, 0, 0, 0, 0, -1)))

    def age(self, **kw):
        return radar._age(self.NOW - int(timedelta(**kw).total_seconds()), self.NOW)

    def test_vocabulary(self):
        self.assertEqual(self.age(seconds=5), "just now")
        self.assertEqual(self.age(minutes=8), "8 min ago")
        self.assertEqual(self.age(minutes=46), "46 min ago")
        self.assertEqual(self.age(hours=1, minutes=20), "1h 20m ago")
        self.assertEqual(self.age(hours=2), "2h ago")

    def test_never_negative_on_clock_skew(self):
        self.assertEqual(radar._age(self.NOW + 300, self.NOW), "just now")

    def test_elapsed_beats_the_calendar_under_an_hour(self):
        # 23:50 yesterday, seen at 00:10 today: "20 min ago", not "yesterday"
        now = int(time.mktime((2026, 9, 23, 0, 10, 0, 0, 0, -1)))
        ts = int(time.mktime((2026, 9, 22, 23, 50, 0, 0, 0, -1)))
        self.assertEqual(radar._age(ts, now), "20 min ago")

    def test_calendar_days_past_an_hour(self):
        now = int(time.mktime((2026, 9, 23, 10, 0, 0, 0, 0, -1)))
        y = int(time.mktime((2026, 9, 22, 22, 0, 0, 0, 0, -1)))
        self.assertEqual(radar._age(y, now), "yesterday")
        old = int(time.mktime((2026, 9, 19, 10, 0, 0, 0, 0, -1)))
        self.assertEqual(radar._age(old, now), "4 days ago")


class Lanes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # prompts.py owns the `prompt` table and opens its OWN connection, so
        # it has its own DB path -- stubbing radar.DB alone left the lane tests
        # reading the REAL log, and real prompts appeared in the assertions.
        import prompts
        self.tmp_db = os.path.join(self.tmp, "radar.db")
        self._db = (radar.DB, prompts.DB)
        radar.DB = prompts.DB = self.tmp_db
        # The Notifications lane reads the real ~/.claude/sessions and Slack's
        # own state file. Point both at nothing, or these assertions depend on
        # whether the machine running them happens to have unread Slack.
        import notify
        self._srcs = (notify.SESSIONS, notify.SLACK_STATE)
        notify.SESSIONS = os.path.join(self.tmp, "none", "*.json")
        notify.SLACK_STATE = os.path.join(self.tmp, "none.json")

    def tearDown(self):
        import notify
        import prompts
        radar.DB, prompts.DB = self._db
        notify.SESSIONS, notify.SLACK_STATE = self._srcs

    def add(self, key, label, ago_s, target="sock|1|%0"):
        con = radar._open()
        con.execute("BEGIN IMMEDIATE")
        con.execute("INSERT INTO event(ts, key, label, target) VALUES(?,?,?,?)",
                    (int(time.time()) - ago_s, key, label, target))
        con.execute("COMMIT")
        con.close()

    def lanes(self):
        return {c["title"]: [e["id"] for e in c["entries"]]
                for c in radar.payload()["columns"]}

    def test_empty_log_still_yields_three_lanes(self):
        self.assertEqual(list(self.lanes()),
                         ["Now", "Touched today", "Notifications"])

    def test_the_lanes_split_on_two_hours(self):
        self.add("$0:@1", "an hour ago", 3600)
        self.add("$0:@2", "a minute ago", 60)
        self.add("$0:@3", "this morning", 5 * 3600)
        lanes = self.lanes()
        # Now is a WINDOW, not the single latest row: two things touched in the
        # last two hours are both what you are working on.
        self.assertEqual(lanes["Now"], ["$0:@2", "$0:@1"])
        self.assertEqual(lanes["Touched today"], ["$0:@3"])

    def test_an_activity_falls_in_exactly_one_lane(self):
        # a duplicated id would border two cards at once, because compose()
        # matches the selection with no column filter. One row per activity at
        # its LATEST touch is what makes that structural rather than a rule.
        for ago in (60, 3600, 2 * 3600 - 1, 2 * 3600 + 1, 5 * 3600, 15 * 3600):
            self.add("$0:@%d" % ago, "w", ago)
        lanes = self.lanes()
        both = set(lanes["Now"]) & set(lanes["Touched today"])
        self.assertEqual(both, set())
        self.assertEqual(len(lanes["Now"]), 3)
        self.assertEqual(len(lanes["Touched today"]), 3)

    def test_older_than_sixteen_hours_is_in_neither_lane(self):
        self.add("$0:@1", "yesterday", 17 * 3600)
        self.add("$0:@2", "now", 60)
        lanes = self.lanes()
        self.assertEqual(lanes["Now"], ["$0:@2"])
        self.assertEqual(lanes["Touched today"], [])

    def test_the_latest_touch_decides_the_lane(self):
        # Touched this morning AND a minute ago: it is current, not history.
        self.add("$0:@1", "w", 5 * 3600)
        self.add("$0:@1", "w", 60)
        self.assertEqual(self.lanes()["Now"], ["$0:@1"])

    def test_ids_are_unique_across_the_whole_payload(self):
        for i in range(4):
            self.add("$0:@%d" % i, "w%d" % i, 60 * (i + 1))
        self.add("$0:@9", "old", 3 * 86400)
        ids = [e for lane in self.lanes().values() for e in lane]
        self.assertEqual(len(ids), len(set(ids)))

    def test_a_lane_dedupes_to_the_latest(self):
        self.add("$0:@1", "old label", 4 * 3600)
        self.add("$0:@1", "new label", 3 * 3600)
        self.add("$0:@2", "other", 60)
        lanes = self.lanes()
        self.assertEqual(lanes["Touched today"].count("$0:@1"), 1)
        card = [e for c in radar.payload()["columns"] for e in c["entries"]
                if e["id"] == "$0:@1"][0]
        self.assertEqual(card["text"], "new label")

    def test_a_note_has_no_target_and_is_tinted(self):
        radar.note("  Talked with John   about HIL timing ")
        card = radar.payload()["columns"][0]["entries"][0]
        self.assertEqual(card["text"], "Talked with John about HIL timing")
        self.assertEqual(card["target"], "")
        self.assertEqual(card["tint"], "note")
        self.assertTrue(card["id"].startswith("note:"))

    def test_two_identical_notes_stay_two_activities(self):
        radar.note("same words")
        radar.note("same words")
        ids = [e for lane in self.lanes().values() for e in lane]
        self.assertEqual(len(set(ids)), 2)

    def test_forget_drops_every_event_for_one_key(self):
        self.add("$0:@1", "gone", 300)
        self.add("$0:@1", "gone", 200)
        self.add("$0:@2", "stays", 100)
        radar.forget("$0:@1")
        ids = [e for lane in self.lanes().values() for e in lane]
        self.assertEqual(ids, ["$0:@2"])

    def test_a_forgotten_window_returns_when_focused_again(self):
        self.add("$0:@1", "w", 300)
        radar.forget("$0:@1")
        self.add("$0:@1", "w", 1)
        self.assertEqual(self.lanes()["Now"], ["$0:@1"])

    def test_payload_matches_the_board_envelope(self):
        self.add("$0:@1", "w", 60)
        p = radar.payload()
        self.assertEqual(set(p), {"title", "columns"})
        for c in p["columns"]:
            self.assertEqual(set(c), {"title", "accent", "entries"})
            for e in c["entries"]:
                self.assertEqual(set(e),
                                 {"text", "meta", "dim", "id", "tint", "target"})
                self.assertNotEqual(e["id"], "")   # selection needs a real id


if __name__ == "__main__":
    unittest.main()
