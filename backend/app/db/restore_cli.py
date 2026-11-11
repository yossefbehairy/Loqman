"""
Offline restore CLI:
    python -m backend.app.db.restore_cli BACKUP.db TARGET.db --yes-app-is-stopped
See docs/BACKUP-RESTORE.md.
"""

import argparse
import sys
from pathlib import Path

from backend.app.db.backup import RestoreError, restore_database


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Restore an AgentOS SQLite backup (offline only).")
    p.add_argument("backup", type=Path)
    p.add_argument("target", type=Path, help="Existing agentos.db to overwrite")
    p.add_argument("--yes-app-is-stopped", action="store_true", required=True)
    args = p.parse_args(argv)
    try:
        pre = restore_database(args.backup, args.target)
    except RestoreError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Restored. Pre-restore backup: {pre}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
