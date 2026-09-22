"""Where things live. RHYTHM_VAULT is baked into the nix wrapper at build time
because $HOME_VAULT is not present in the environment Hyprland execs into."""
import os
import sys

_v = os.environ.get("RHYTHM_VAULT") or os.environ.get("HOME_VAULT")
if not _v:
    sys.exit("rhythm: neither RHYTHM_VAULT nor HOME_VAULT is set")

VAULT = _v
UNSORTED = os.path.join(VAULT, "unsorted")
BOARD = os.path.join(VAULT, "kanban", "tiberius_kanban.md")


def atomic_write(path, text):
    """Write via a sibling temp file + rename, so a crash never truncates the
    target. Callers watching this path must watch the parent DIRECTORY: the
    rename orphans the file's inode and kills any inode-bound watch."""
    tmp = path + ".rhythm-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
