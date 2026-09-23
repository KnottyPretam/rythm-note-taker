"""The notifications lane: liveness, the two Slack readers, and the lane itself.

No network. The Claude sessions directory and the Slack state file are faked in
a temp dir; liveness is tested against THIS process, which is the only pid we
can be certain about.
"""
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "rhythm"))
os.environ.setdefault("RHYTHM_VAULT", "/nonexistent-vault-for-tests")

import notify  # noqa: E402
import radar   # noqa: E402


def _procstart(pid):
    with open("/proc/%d/stat" % pid) as f:
        stat = f.read()
    # after the LAST ')', which drops fields 1-2, so starttime is index 19
    return stat[stat.rindex(")") + 2:].split()[19]


class Liveness(unittest.TestCase):
    """The lane has no heartbeat to fall back on: nothing rewrites a session
    file except a status change, so a killed session leaves a file identical to
    a live one forever. The pid check is the only thing preventing ghosts."""

    def setUp(self):
        self.pid = os.getpid()
        self.domain = notify._domain()
        self.s = {"pid": self.pid, "procStart": _procstart(self.pid),
                  "pidDomain": self.domain}

    def test_this_process_is_live(self):
        self.assertTrue(notify._live(self.s, self.domain))

    def test_a_dead_pid_is_not(self):
        self.assertFalse(notify._live(dict(self.s, pid=0x7FFFFFF), self.domain))

    def test_a_recycled_pid_is_not(self):
        # right pid, wrong start time -- the pid was reused by something else
        self.assertFalse(notify._live(dict(self.s, procStart="1"), self.domain))

    def test_another_machine_is_not(self):
        self.assertFalse(notify._live(dict(self.s, pidDomain="linux:other:pid:[1]"),
                                      self.domain))

    def test_procstart_as_an_int_still_matches(self):
        # the silent-empty trap: a type mismatch would make EVERY session look
        # recycled and the lane would read as "nothing is waiting"
        self.assertTrue(notify._live(dict(self.s, procStart=int(self.s["procStart"])),
                                     self.domain))

    def test_junk_never_raises(self):
        for bad in ({}, {"pid": "x"}, {"pid": None}, dict(self.s, procStart=None)):
            self.assertIs(notify._live(bad, self.domain), False)


class ClaudeSessions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._g = notify.SESSIONS
        notify.SESSIONS = os.path.join(self.tmp, "*.json")
        self.pid, self.domain = os.getpid(), notify._domain()

    def tearDown(self):
        notify.SESSIONS = self._g

    def write(self, name, **kw):
        s = {"pid": self.pid, "procStart": _procstart(self.pid),
             "pidDomain": self.domain, "sessionId": name, "name": name,
             "cwd": "/home/x/" + name, "status": "waiting",
             "statusUpdatedAt": int(time.time()) * 1000}
        s.update(kw)
        with open(os.path.join(self.tmp, name + ".json"), "w") as f:
            json.dump(s, f)

    def test_only_waiting_sessions_appear(self):
        self.write("a", status="waiting")
        self.write("b", status="busy")
        self.write("c", status="idle")
        self.assertEqual([r[0] for r in notify.claude_waiting()], ["claude:a"])

    def test_a_ghost_is_hidden(self):
        self.write("ghost", pid=0x7FFFFFF)
        self.assertEqual(notify.claude_waiting(), [])

    def test_pane_comes_from_the_tmux_field(self):
        # "Home:@2.%2" -> "%2". split(':') would yield "@2.%2", which the Lua
        # target regex rejects silently rather than erroring.
        self.write("a", tmux="Home:@2.%2")
        self.assertEqual(notify.claude_waiting()[0][4], "%2")

    def test_a_session_with_no_tmux_field_has_no_pane(self):
        self.write("a")
        self.assertEqual(notify.claude_waiting()[0][4], "")

    def test_a_window_id_is_rejected_as_a_pane(self):
        self.write("a", tmux="Home:@2")
        self.assertEqual(notify.claude_waiting()[0][4], "")

    def test_why_prefers_needs_then_waitingFor(self):
        self.write("a", needs="choose: allow or deny")
        self.assertEqual(notify.claude_waiting()[0][2], "choose: allow or deny")
        self.write("b", waitingFor="input needed")
        why = {r[0]: r[2] for r in notify.claude_waiting()}
        self.assertEqual(why["claude:b"], "input needed")

    def test_unreadable_json_is_skipped_not_fatal(self):
        self.write("good")
        with open(os.path.join(self.tmp, "torn.json"), "w") as f:
            f.write("{not json")
        self.assertEqual([r[0] for r in notify.claude_waiting()], ["claude:good"])


class SlackState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._p = notify.SLACK_STATE
        notify.SLACK_STATE = os.path.join(self.tmp, "root-state.json")

    def tearDown(self):
        notify.SLACK_STATE = self._p

    def write(self, teams, workspaces=None):
        with open(notify.SLACK_STATE, "w") as f:
            json.dump({"webapp": {"teams": teams},
                       "workspaces": workspaces or {}}, f)

    def test_only_workspaces_with_unreads(self):
        self.write({"T1": {"unreads": {"unreads": 0, "unreadHighlights": 0}},
                    "T2": {"unreads": {"unreads": 3, "unreadHighlights": 0}}},
                   {"T2": {"domain": "gov-tiberius"}})
        got = notify.slack_unread()
        self.assertEqual([r[0] for r in got], ["slack:T2"])
        self.assertEqual(got[0][1], "Slack · gov-tiberius")
        self.assertEqual(got[0][2], "3 unread")

    def test_highlights_are_called_out(self):
        self.write({"T1": {"unreads": {"unreads": 3, "unreadHighlights": 1}}},
                   {"T1": {"domain": "hq"}})
        self.assertEqual(notify.slack_unread()[0][2], "3 unread, 1 @you")

    def test_a_mention_with_no_unread_still_counts(self):
        self.write({"T1": {"unreads": {"unreads": 0, "unreadHighlights": 2}}})
        self.assertEqual(len(notify.slack_unread()), 1)

    def test_missing_file_is_empty_not_fatal(self):
        notify.SLACK_STATE = "/nonexistent"
        self.assertEqual(notify.slack_unread(), [])

    def test_falls_back_to_the_team_id_when_unnamed(self):
        self.write({"T9": {"unreads": {"unreads": 1}}})
        self.assertEqual(notify.slack_unread()[0][1], "Slack · T9")


class Notices(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._db = radar.DB
        radar.DB = os.path.join(self.tmp, "radar.db")
        self._g, self._p = notify.SESSIONS, notify.SLACK_STATE
        notify.SESSIONS = os.path.join(self.tmp, "none", "*.json")
        notify.SLACK_STATE = os.path.join(self.tmp, "none.json")

    def tearDown(self):
        radar.DB = self._db
        notify.SESSIONS, notify.SLACK_STATE = self._g, self._p

    def add(self, label, where, ago=0):
        con = radar._open()
        con.execute("BEGIN IMMEDIATE")
        con.execute("INSERT INTO notice(ts, key, label, meta) VALUES(?,?,?,?)"
                    " ON CONFLICT(key) DO UPDATE SET ts = excluded.ts",
                    (int(time.time()) - ago, "mako:" + label, label, where))
        con.execute("COMMIT")
        con.close()

    def lane(self):
        return radar.payload()["columns"][2]["entries"]

    def test_the_third_lane_is_notifications(self):
        self.assertEqual([c["title"] for c in radar.payload()["columns"]],
                         ["Now", "Touched today", "Notifications"])

    def test_a_notice_appears(self):
        self.add("Lucas Pick", "gov-tiberius")
        e = self.lane()[0]
        self.assertEqual(e["text"], "Lucas Pick")
        self.assertEqual(e["id"], "mako:Lucas Pick")
        self.assertEqual(e["target"], "")

    def test_a_repeat_collapses_to_one_card(self):
        self.add("Lucas Pick", "gov", ago=600)
        self.add("Lucas Pick", "gov", ago=0)
        self.assertEqual(len(self.lane()), 1)
        self.assertEqual(self.lane()[0]["meta"], "just now")

    def test_notices_older_than_a_week_drop_off(self):
        self.add("stale", "gov", ago=8 * 86400)
        self.add("fresh", "gov", ago=60)
        self.assertEqual([e["text"] for e in self.lane()], ["fresh"])

    def test_forget_removes_a_notice(self):
        self.add("Dave", "hq")
        radar.forget("mako:Dave")
        self.assertEqual(self.lane(), [])

    def test_empty_sources_leave_an_empty_lane_not_an_error(self):
        self.assertEqual(self.lane(), [])

    def test_ids_are_unique_across_the_whole_payload(self):
        self.add("Dave", "hq")
        con = radar._open()
        con.execute("BEGIN IMMEDIATE")
        con.execute("INSERT INTO event(ts, key, label, target) VALUES(?,?,?,?)",
                    (int(time.time()), "$0:@1", "w", "sock|1|%0"))
        con.execute("COMMIT")
        con.close()
        ids = [e["id"] for c in radar.payload()["columns"] for e in c["entries"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(ids))


if __name__ == "__main__":
    unittest.main()
