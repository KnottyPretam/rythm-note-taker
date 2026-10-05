"""Window focus, for the activities that are not tmux panes.

The tmux hook cannot see two things you asked to track: the SUPER+G chat agents
are Chromium web apps, and the ssh terminal runs a *remote* tmux, so no local
hook ever fires for it. Hyprland's event socket sees both.

Verified behaviour: `activewindow>>CLASS,TITLE` arrives first, then
`activewindowv2>>ADDRESS` -- and the v2 address has NO `0x` prefix, unlike the
one `hyprctl clients` prints. An idle subscription receives nothing at all, so
this costs nothing when nothing happens.

Stdlib only, and imports nothing else from the package: this is a daemon with
no vault.
"""
import os
import socket
import sqlite3
import sys
import time

DB = os.path.join(os.environ.get("XDG_STATE_HOME",
                                 os.path.expanduser("~/.local/state")),
                  "rhythm", "radar.db")

CHAT = {"chrome-chatgpt.com__-Default": "ChatGPT",
        "chrome-claude.ai__-Default": "Claude",
        "chrome-grok.com__-Default": "Grok"}
SLACK = "slack"
TERM = "com.mitchellh.ghostty"
LOCAL_TMUX = "tmux"     # the local tmux window's exact title; the hook owns it
DEBOUNCE = 10           # seconds; alt-tabbing back and forth is one visit


def _sock():
    run = os.environ.get("XDG_RUNTIME_DIR") or "/run/user/%d" % os.getuid()
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not sig:
        sys.exit("rhythm: HYPRLAND_INSTANCE_SIGNATURE is not set")
    return os.path.join(run, "hypr", sig, ".socket2.sock")


def classify(cls, title):
    """(key, label) for a window worth recording, or None.

    An allowlist, not a denylist: this runs on every focus change all day, and
    a radar that logged every window would be a keystroke-adjacent record of
    the whole session rather than a working set.
    """
    title = " ".join((title or "").split())
    if cls in CHAT:
        # Keyed by CLASS, so the chat agent is one card across restarts. The
        # Chromium title of a web app is the conversation name.
        name = CHAT[cls]
        # A web app with no conversation open titles itself after the app, and
        # "Grok · Grok" reads like a bug.
        head = title if title and title != name else ""
        return "win:" + cls, "%s · %s" % (name, head) if head else name
    if cls == SLACK:
        head = title.rsplit(" - Slack", 1)[0]
        return "win:" + cls, "Slack · %s" % head if head else "Slack"
    if cls == TERM:
        if title == LOCAL_TMUX:
            return None         # the pane-focus-in hook already owns this one
        # Keyed by ADDRESS, not class: there are several of these -- a remote
        # tmux over ssh and bare Claude sessions -- and they are different
        # activities. The title is the only thing that tells them apart, and
        # all Ghostty windows share one PID, so /proc cannot say which is which.
        return None, title or "terminal"
    return None


def _open():
    first = not os.path.exists(DB)
    if first:
        os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = sqlite3.connect(DB, isolation_level=None)
    con.execute("PRAGMA busy_timeout = 5000")
    con.execute("PRAGMA synchronous = NORMAL")
    if first:
        con.execute("PRAGMA journal_mode = WAL")
    con.execute("CREATE TABLE IF NOT EXISTS event ("
                "id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, key TEXT NOT NULL,"
                " label TEXT NOT NULL, target TEXT)")
    con.execute("CREATE INDEX IF NOT EXISTS event_ts ON event(ts)")
    return con


def record(key, label, addr):
    con = _open()
    con.execute("BEGIN IMMEDIATE")
    # `win|` targets are what let Enter focus the window again. The address is
    # rewritten on every visit, so a chat agent keyed by class still carries a
    # LIVE address even after the app was restarted under a new one.
    con.execute("INSERT INTO event(ts, key, label, target) VALUES(?,?,?,?)",
                (int(time.time()), key, label, "win|" + addr))
    con.execute("COMMIT")
    con.close()


def watch():
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(_sock())
    buf, pending, last = b"", None, {}
    while True:
        chunk = s.recv(8192)
        if not chunk:
            # The compositor went away. Exiting non-zero is the point: systemd
            # restarts us against the new instance, whose signature differs.
            sys.exit("rhythm: hyprland event socket closed")
        buf += chunk
        *lines, buf = buf.split(b"\n")
        for raw in lines:
            name, _, rest = raw.decode("utf-8", "replace").partition(">>")
            if name == "activewindow":
                cls, _, title = rest.partition(",")
                pending = classify(cls, title) if cls else None
            elif name == "activewindowv2" and pending:
                key, label = pending
                pending = None
                if not rest:
                    continue
                key = key or "win:" + rest     # terminals are keyed by address
                now = time.time()
                if now - last.get(key, 0) < DEBOUNCE:
                    continue
                last[key] = now
                record(key, label, rest)
