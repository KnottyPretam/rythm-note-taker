"""Prompts you sent to an agent, and whether you have looked at the answer.

Fed by Claude Code hooks rather than by reading files. `UserPromptSubmit` and
`Stop`/`StopFailure` both carry a `prompt_id`, which joins a prompt to the turn
that ends it -- and `source` distinguishes a prompt you typed from one the
system injected, which ~/.claude/history.jsonl cannot do at all.

Stdlib only, and imports nothing else from the package: these run from a hook.
"""
import glob
import json
import os
import sqlite3
import time

SESSIONS = os.path.expanduser("~/.claude/sessions/*.json")
DB = os.path.join(os.environ.get("XDG_STATE_HOME",
                                 os.path.expanduser("~/.local/state")),
                  "rhythm", "radar.db")

STALE = 7 * 86400       # a week-old notification is noise, not a reminder

FINISHED_SQL = """
SELECT pid, text, agent, done_ts, win FROM prompt
WHERE slash = 0 AND done_ts IS NOT NULL AND done_ts >= ?
ORDER BY done_ts DESC"""

SENT_SQL = """
SELECT pid, text, agent, ts, win FROM prompt
WHERE slash = 0 AND ts >= ? ORDER BY ts DESC"""


def _open():
    first = not os.path.exists(DB)
    if first:
        os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = sqlite3.connect(DB, isolation_level=None)
    con.execute("PRAGMA busy_timeout = 5000")
    con.execute("PRAGMA synchronous = NORMAL")
    if first:
        con.execute("PRAGMA journal_mode = WAL")
    con.execute("CREATE TABLE IF NOT EXISTS prompt ("
                "id INTEGER PRIMARY KEY, pid TEXT NOT NULL UNIQUE,"
                " ts INTEGER NOT NULL, done_ts INTEGER, ok INTEGER,"
                " agent TEXT NOT NULL, win TEXT, text TEXT NOT NULL,"
                " slash INTEGER NOT NULL DEFAULT 0)")
    return con


def _session(sid):
    """(name, window_id) for a session id, from Claude's own registry."""
    for path in glob.glob(SESSIONS):
        try:
            with open(path) as f:
                s = json.load(f)
        except (OSError, ValueError):
            continue
        if s.get("sessionId") != sid:
            continue
        # "Home:@2.%2" -> window id "@2". The session NAME on the left is not
        # the session ID the focus log keys on, so only the window id is usable.
        tmux = s.get("tmux") or ""
        win = tmux.partition(":")[2].partition(".")[0] or None
        return s.get("name") or os.path.basename(s.get("cwd") or "") or "claude", win
    return "claude", None


def sent(h):
    """UserPromptSubmit. `h` is the hook's stdin payload."""
    pid = h.get("prompt_id")
    text = " ".join((h.get("prompt") or "").split())
    if not pid or not text:
        return
    # `source` names who produced the turn: user | sdk | system | loop_wakeup |
    # schedule_wakeup | poll_event. Anything but `user` is machine-injected --
    # a task notification, a /loop wakeup -- and is not a prompt you sent. The
    # field may be absent while it rolls out, so absence is treated as human.
    if h.get("source") not in (None, "user"):
        return
    agent, win = _session(h.get("session_id") or "")
    con = _open()
    con.execute("BEGIN IMMEDIATE")
    # DO NOTHING, not REPLACE: an expansion tombstone may already be here, and
    # the two hooks have no guaranteed order.
    con.execute("INSERT INTO prompt(pid, ts, agent, win, text)"
                " VALUES(?,?,?,?,?) ON CONFLICT(pid) DO NOTHING",
                (pid, int(time.time()), agent, win, text))
    con.execute("COMMIT")
    con.close()


def expanded(h):
    """UserPromptExpansion. A slash command is you operating the tool, not
    asking it something, so it is marked and never shown.

    Order-independent by design: this writes a tombstone if the submit hook has
    not run yet, and updates the row if it has."""
    pid = h.get("prompt_id")
    if not pid or h.get("expansion_type") != "slash_command":
        return
    con = _open()
    con.execute("BEGIN IMMEDIATE")
    con.execute("INSERT INTO prompt(pid, ts, agent, text, slash)"
                " VALUES(?,?,'','',1) ON CONFLICT(pid) DO UPDATE SET slash = 1",
                (pid, int(time.time())))
    con.execute("COMMIT")
    con.close()


def done(h, ok=True):
    """Stop, or StopFailure with ok=False.

    A turn parked on background work has NOT finished -- `background_tasks` is
    what tells the two apart, and without it a session waiting on a subagent
    would look answered. StopFailure matters for the opposite reason: a turn
    that dies never fires Stop, so without it a prompt stays "running" forever.
    """
    pid = h.get("prompt_id")
    if not pid:
        return
    if ok and h.get("background_tasks"):
        return                      # parked, not done
    con = _open()
    con.execute("BEGIN IMMEDIATE")
    con.execute("UPDATE prompt SET done_ts = ?, ok = ? WHERE pid = ?",
                (int(time.time()), 1 if ok else 0, pid))
    con.execute("COMMIT")
    con.close()


def sent_since(con, since):
    return con.execute(SENT_SQL, (since,)).fetchall()


def _looked(key, label, win, agent):
    """Did this focus event mean you looked at that prompt's agent?"""
    if win:
        # A tmux session. event.key is "$0:@2" and only the window id is
        # comparable, because a session file knows the session NAME while the
        # focus log keys on the session ID. Exact equality after the colon --
        # never LIKE, whose wildcards '%' and '_' are both literal in a pane id.
        return key.partition(":")[2] == win
    # No tmux field, so the pane is unknowable -- but Claude Code puts the
    # session name in the TERMINAL TITLE, and the Hyprland focus reader stores
    # that title as the label. The session named "home-agent" is the window
    # titled "✳ home-agent". A heuristic, and the only link there is.
    return bool(agent) and key.startswith("win:") and agent in label


def unviewed(con, now=None):
    """Finished prompts you have not looked at since they finished.

    Done in Python rather than SQL: the rule has two cases, the volumes are a
    few hundred rows, and the SQL that expressed both was unreadable.
    """
    now = int(time.time()) if now is None else now
    rows = con.execute(FINISHED_SQL, (now - STALE,)).fetchall()
    if not rows:
        return []
    # Bounded by the OLDEST unviewed prompt, which STALE in turn bounds.
    since = min(r[3] for r in rows)
    focus = con.execute("SELECT key, label, ts FROM event WHERE ts >= ?",
                        (since,)).fetchall()
    return [r for r in rows
            if not any(ts >= r[3] and _looked(key, label or "", r[4], r[2])
                       for key, label, ts in focus)]
