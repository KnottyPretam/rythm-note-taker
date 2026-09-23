"""rhythm -- note capture and kanban sync.

  rhythm new                      create today's next note, print its path
  rhythm sync [file:lnum]         fold markers from unsorted/ into the board
  rhythm board [activity|project] a board as JSON, for the nvim renderer
  rhythm migrate                  one-off: split the old mixed columns
  rhythm remove <id>              drop a card, and ignore its marker from now on
  rhythm remind-route             one digest notification for the Route column
  rhythm radar                    the activity radar as JSON, for the renderer
  rhythm radar-tick <pane> <t0>   record a focus event (the tmux hook only)
  rhythm radar-note <text...>     record an offline activity
  rhythm radar-forget <key>       drop an activity from the radar
  rhythm notify-tick <mako-id>    record a notification (the mako hook only)
"""
import subprocess
import sys


def _usage():
    sys.exit(__doc__.strip())


def main(argv):
    if not argv:
        _usage()
    cmd, rest = argv[0], argv[1:]

    if cmd == "new":
        import notes
        print(notes.new_note_path())

    elif cmd == "sync":
        import board
        skip = None
        if rest:
            path, _, lnum = rest[0].rpartition(":")
            if path and lnum.isdigit():
                skip = (path, int(lnum))
        board.sync(skip)

    elif cmd == "board":
        import json
        import board
        view = rest[0] if rest else "activity"
        if view not in ("activity", "project"):
            _usage()
        # Read only. Never sync from here.
        json.dump(board.payload(view), sys.stdout)

    elif cmd == "migrate":
        import board
        moved, backup = board.migrate()
        if backup is None:
            print("rhythm: nothing to migrate")
        else:
            print(f"rhythm: moved {moved} card(s); backup at {backup}")

    elif cmd == "remove":
        import board
        if not rest:
            _usage()
        if not board.remove(rest[0]):
            return 1

    elif cmd == "radar":
        import json
        import radar
        json.dump(radar.payload(), sys.stdout)

    elif cmd == "radar-tick":
        import radar
        # Called from a tmux hook on every pane focus. Anything that goes wrong
        # here must stay silent: run-shell -b discards stdout, and a traceback
        # on a hot path would be invisible noise. A dropped event is harmless --
        # the next focus records the same activity.
        if len(rest) < 2 or not rest[1].isdigit():
            return 0
        try:
            radar.tick(rest[0], int(rest[1]))
        except Exception:
            return 0

    elif cmd == "notify-tick":
        import radar
        # Called from mako's on-notify hook on every matching notification.
        # Silent on any failure, for the same reason radar-tick is: a hot path
        # whose output goes nowhere must not raise.
        if not rest:
            return 0
        try:
            radar.notice_tick(rest[0])
        except Exception:
            return 0

    elif cmd == "radar-note":
        import radar
        radar.note(" ".join(rest))

    elif cmd == "radar-forget":
        import radar
        if not rest:
            _usage()
        radar.forget(rest[0])

    elif cmd == "remind-route":
        import board
        tasks = board.route_tasks()
        if not tasks:
            return 0          # nothing to pass along; say nothing
        body = "\n".join("  " + t for t in tasks)
        # One digest, not one per task: mako runs on defaults, where
        # max-visible is 5 and notifications never expire, so a burst would
        # hide its own tail behind the first five.
        subprocess.run(
            ["notify-send", "--app-name=rhythm",
             f"Route · {len(tasks)} to pass along", body],
            check=False,
        )

    else:
        _usage()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
