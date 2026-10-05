"""Prompts: the hook handlers, and the rule that clears a notification.

Hook payloads in, lanes out. No network, no real Claude, a temp DB -- and the
live sources notify.py reads are stubbed away, because a test that consults the
real ~/.claude/sessions passes or fails depending on what you happen to be
running at the time.
"""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "rhythm"))
os.environ.setdefault("RHYTHM_VAULT", "/nonexistent-vault-for-tests")

import focus    # noqa: E402
import notify   # noqa: E402
import prompts  # noqa: E402
import radar    # noqa: E402

TMUX_SESSION = {"sessionId": "s-tmux", "name": "rhythm", "tmux": "Home:@2.%2"}
BARE_SESSION = {"sessionId": "s-bare", "name": "home-agent", "cwd": "/home/p/x"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        db = os.path.join(self.tmp, "radar.db")
        self._dbs = (prompts.DB, radar.DB, focus.DB, prompts.SESSIONS)
        prompts.DB = radar.DB = focus.DB = db
        # A session registry of our own, so the handler resolves a known agent.
        reg = os.path.join(self.tmp, "sessions")
        os.makedirs(reg)
        for s in (TMUX_SESSION, BARE_SESSION):
            with open(os.path.join(reg, s["sessionId"] + ".json"), "w") as f:
                json.dump(s, f)
        prompts.SESSIONS = os.path.join(reg, "*.json")
        # notify.py reads the LIVE Slack state and the LIVE session registry.
        self._srcs = (notify.SESSIONS, notify.SLACK_STATE)
        notify.SESSIONS = os.path.join(self.tmp, "none", "*.json")
        notify.SLACK_STATE = os.path.join(self.tmp, "none", "state.json")
        radar._open().close()          # `event` must exist for the join

    def tearDown(self):
        prompts.DB, radar.DB, focus.DB, prompts.SESSIONS = self._dbs
        notify.SESSIONS, notify.SLACK_STATE = self._srcs

    def sent(self, pid, text="do a thing", sid="s-tmux", **kw):
        h = {"prompt_id": pid, "session_id": sid, "prompt": text, "source": "user"}
        h.update(kw)
        prompts.sent(h)

    def rows(self):
        con = prompts._open()
        try:
            return con.execute("SELECT pid, agent, win, slash, text, done_ts, ok"
                               " FROM prompt ORDER BY pid").fetchall()
        finally:
            con.close()

    def lane(self, title):
        cols = {c["title"]: c["entries"] for c in radar.payload()["columns"]}
        return cols[title]


class Capture(Base):
    def test_records_agent_and_window(self):
        self.sent("p1")
        self.assertEqual(self.rows()[0][1:3], ("rhythm", "@2"))

    def test_session_without_tmux_has_no_window(self):
        self.sent("p1", sid="s-bare")
        self.assertEqual(self.rows()[0][1:3], ("home-agent", None))

    def test_machine_injected_turns_are_not_prompts(self):
        # This is the whole reason for using the hook over history.jsonl: a
        # task notification is indistinguishable from a typed prompt in the file.
        for src in ("system", "sdk", "loop_wakeup", "schedule_wakeup", "poll_event"):
            self.sent("p-" + src, source=src)
        self.assertEqual(self.rows(), [])

    def test_absent_source_is_treated_as_human(self):
        prompts.sent({"prompt_id": "p1", "session_id": "s-tmux", "prompt": "hi"})
        self.assertEqual(len(self.rows()), 1)

    def test_empty_prompt_is_dropped(self):
        self.sent("p1", text="   ")
        self.assertEqual(self.rows(), [])

    def test_unknown_session_still_records(self):
        self.sent("p1", sid="nope")
        self.assertEqual(self.rows()[0][1:3], ("claude", None))


class SlashCommands(Base):
    """The two hooks have no guaranteed order, so both must work."""

    def test_expansion_after_submit(self):
        self.sent("p1", text="/plan")
        prompts.expanded({"prompt_id": "p1", "expansion_type": "slash_command"})
        self.assertEqual(self.rows()[0][3], 1)

    def test_expansion_before_submit(self):
        prompts.expanded({"prompt_id": "p1", "expansion_type": "slash_command"})
        self.sent("p1", text="/plan")
        self.assertEqual(self.rows()[0][3], 1)   # the tombstone is not overwritten

    def test_other_expansions_are_left_alone(self):
        self.sent("p1", text="look at @file.py")
        prompts.expanded({"prompt_id": "p1", "expansion_type": "at_mention"})
        self.assertEqual(self.rows()[0][3], 0)

    def test_slash_commands_reach_neither_lane(self):
        self.sent("p1", text="/effort")
        prompts.expanded({"prompt_id": "p1", "expansion_type": "slash_command"})
        prompts.done({"prompt_id": "p1"})
        self.assertEqual(self.lane("Now"), [])
        self.assertEqual(self.lane("Touched today"), [])
        self.assertEqual(self.lane("Notifications"), [])


class Completion(Base):
    def test_parked_on_background_work_is_not_done(self):
        self.sent("p1")
        prompts.done({"prompt_id": "p1", "background_tasks": [{"id": "b1"}]})
        self.assertIsNone(self.rows()[0][5])
        self.assertEqual(len(self.lane("Now")), 1)
        self.assertEqual(self.lane("Notifications"), [])

    def test_stop_finishes_it(self):
        self.sent("p1")
        prompts.done({"prompt_id": "p1", "background_tasks": []})
        self.assertEqual(self.rows()[0][6], 1)
        self.assertEqual(len(self.lane("Notifications")), 1)

    def test_a_turn_that_died_still_finishes(self):
        # StopFailure, not Stop -- a failed turn never fires Stop, and without
        # this the prompt would read as "still running" forever.
        self.sent("p1")
        prompts.done({"prompt_id": "p1"}, ok=False)
        self.assertIsNotNone(self.rows()[0][5])
        self.assertEqual(self.rows()[0][6], 0)
        self.assertEqual(len(self.lane("Notifications")), 1)


class AutoClear(Base):
    """A finished prompt with no focus of its window since is UNVIEWED."""

    def finish(self, pid="p1", sid="s-tmux"):
        self.sent(pid, sid=sid)
        prompts.done({"prompt_id": pid, "background_tasks": []})

    def tmux_focus(self, win="@2", when=None):
        con = radar._open()
        con.execute("INSERT INTO event(ts, key, label, target) VALUES(?,?,?,?)",
                    (when or int(time.time()), "$0:" + win, "Home / tools",
                     "/run/user/1000/tmux-1000/default|1|%2"))
        con.close()

    def test_looking_at_the_pane_clears_it(self):
        self.finish()
        self.assertEqual(len(self.lane("Notifications")), 1)
        self.tmux_focus()
        self.assertEqual(self.lane("Notifications"), [])
        self.assertEqual(len(self.lane("Now")), 2)   # moved, not deleted

    def test_a_different_window_does_not_clear_it(self):
        self.finish()
        self.tmux_focus(win="@7")
        self.assertEqual(len(self.lane("Notifications")), 1)

    def test_focus_before_it_finished_does_not_count(self):
        self.tmux_focus(when=int(time.time()) - 600)
        self.finish()
        self.assertEqual(len(self.lane("Notifications")), 1)

    def test_window_id_match_is_exact(self):
        # '@2' must not match '@21'. LIKE would, and a pane id contains a
        # literal '%', which is also a LIKE wildcard.
        self.finish()
        self.tmux_focus(win="@21")
        self.assertEqual(len(self.lane("Notifications")), 1)

    def test_tmuxless_session_clears_via_its_window_title(self):
        # Claude Code puts the session name in the terminal title, and that is
        # the only link a session with no tmux field has to a window.
        self.finish(sid="s-bare")
        self.assertEqual(len(self.lane("Notifications")), 1)
        focus.record("win:addr", "✳ home-agent", "58b5dab00e60")
        self.assertEqual(self.lane("Notifications"), [])

    def test_an_unrelated_window_does_not_clear_it(self):
        self.finish(sid="s-bare")
        focus.record("win:other", "✳ steel-base", "58b5dab0aaaa")
        self.assertEqual(len(self.lane("Notifications")), 1)

    def test_a_week_old_notification_ages_out(self):
        self.finish()
        con = prompts._open()
        con.execute("UPDATE prompt SET done_ts = ?",
                    (int(time.time()) - prompts.STALE - 1,))
        con.close()
        self.assertEqual(self.lane("Notifications"), [])

    def test_x_drops_a_prompt(self):
        # forget() only knew event/notice; a prompt key matched nothing there,
        # so `x` on a prompt card appeared to do nothing at all.
        self.finish()
        radar.forget(self.lane("Notifications")[0]["id"])
        self.assertEqual(self.rows(), [])


class Lanes(Base):
    def test_notifications_owns_an_unviewed_prompt(self):
        # entry.id is the selection key: one id in two lanes borders both.
        self.sent("p1")
        prompts.done({"prompt_id": "p1", "background_tasks": []})
        ids = [e["id"] for c in radar.payload()["columns"] for e in c["entries"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_a_lane_puts_prompts_above_windows_above_panes(self):
        con = radar._open()
        con.execute("INSERT INTO event(ts, key, label, target) VALUES(?,?,?,?)",
                    (int(time.time()), "$0:@9", "Home / tools",
                     "/run/user/1000/tmux-1000/default|1|%9"))
        con.close()
        focus.record("win:chrome-grok.com__-Default", "Grok", "58b5dab0aaaa")
        self.sent("p1")
        texts = [e["text"] for e in self.lane("Now")]
        self.assertEqual(texts[0], '"do a thing"')
        self.assertTrue(texts[-1].startswith("Home /"))

    def test_a_prompt_ages_from_now_into_touched_today(self):
        self.sent("p1")
        self.assertEqual(len(self.lane("Now")), 1)
        con = prompts._open()
        con.execute("UPDATE prompt SET ts = ?",
                    (int(time.time()) - radar.NOW_WINDOW - 60,))
        con.close()
        self.assertEqual(self.lane("Now"), [])
        self.assertEqual(len(self.lane("Touched today")), 1)

    def test_a_prompt_older_than_the_day_window_is_in_neither(self):
        self.sent("p1")
        con = prompts._open()
        con.execute("UPDATE prompt SET ts = ?",
                    (int(time.time()) - radar.TODAY_WINDOW - 60,))
        con.close()
        self.assertEqual(self.lane("Now"), [])
        self.assertEqual(self.lane("Touched today"), [])

    def test_a_long_prompt_is_clipped(self):
        self.sent("p1", text="word " * 200)
        self.assertLessEqual(len(self.lane("Now")[0]["text"]), 75)

    def test_a_window_row_is_tinted_apart_from_a_note(self):
        focus.record("win:slack", "Slack", "58b5dab0aaaa")
        radar.note("talked to a coworker about HIL timing")
        tints = {e["text"]: e["tint"] for c in radar.payload()["columns"]
                 for e in c["entries"]}
        self.assertEqual(tints["Slack"], "win")
        self.assertEqual(tints["talked to a coworker about HIL timing"], "note")

    def test_a_window_focus_does_not_pose_as_the_tmux_server(self):
        # SERVER_SQL took the newest non-empty target, and a win| target is not
        # a tmux server -- which produced targets like "win|<addr>|@2".
        con = radar._open()
        con.execute("INSERT INTO event(ts, key, label, target) VALUES(?,?,?,?)",
                    (int(time.time()) - 5, "$0:@2", "Home / tools",
                     "/run/user/1000/tmux-1000/default|1|%2"))
        con.close()
        focus.record("win:slack", "Slack", "58b5dab0aaaa")
        self.sent("p1")
        self.assertTrue(self.lane("Now")[0]["target"]
                        .startswith("/run/user/1000/tmux-1000/default|1|@2"))


class Classify(unittest.TestCase):
    """The focus allowlist. An allowlist, because a radar that logged every
    window would be a record of the whole session rather than a working set."""

    def test_chat_agents_are_keyed_by_class(self):
        key, label = focus.classify("chrome-chatgpt.com__-Default", "Mixed Precision EKF")
        self.assertEqual(key, "win:chrome-chatgpt.com__-Default")
        self.assertEqual(label, "ChatGPT · Mixed Precision EKF")

    def test_slack_sheds_its_suffix(self):
        self.assertEqual(focus.classify("slack", "GNC Team (DM) - Tiberius - Slack")[1],
                         "Slack · GNC Team (DM) - Tiberius")

    def test_the_local_tmux_window_belongs_to_the_tmux_hook(self):
        self.assertIsNone(focus.classify("com.mitchellh.ghostty", "tmux"))

    def test_other_terminals_are_keyed_by_address(self):
        # There are several: a remote tmux over ssh and bare Claude sessions.
        # A null key means "key me by the address", which only watch() knows.
        key, label = focus.classify("com.mitchellh.ghostty",
                                    'steel-dev:2:base - "✳ steel-base"')
        self.assertIsNone(key)
        self.assertEqual(label, 'steel-dev:2:base - "✳ steel-base"')

    def test_everything_else_is_ignored(self):
        for cls in ("google-chrome", "com.rhythm.note", "org.pwmt.zathura", ""):
            self.assertIsNone(focus.classify(cls, "whatever"))


if __name__ == "__main__":
    unittest.main()
