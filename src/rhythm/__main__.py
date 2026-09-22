"""rhythm -- note capture and kanban sync.

  rhythm new                      create today's next note, print its path
  rhythm sync [file:lnum]         fold markers from unsorted/ into the board
  rhythm board                    the board as JSON, for the nvim renderer
  rhythm remove <id>              drop a card, and ignore its marker from now on
  rhythm remind-route             one digest notification for the Route column
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
        # Read only. Never sync from here.
        json.dump(board.payload(), sys.stdout)

    elif cmd == "remove":
        import board
        if not rest:
            _usage()
        if not board.remove(rest[0]):
            return 1

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
