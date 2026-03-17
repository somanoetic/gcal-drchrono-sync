"""Run shift buffer sync then DrChrono sync in sequence.

Single entry point for Task Scheduler / GitHub Actions.

Usage:
    python run_all.py          # normal run
    python run_all.py --full   # force full re-sync for both steps
"""

import sys

import os
import shift_buffers
import sync
import drchrono_to_gcal
import notify


def _run_one_time_cleanup():
    """Run a one-time cleanup if the marker file exists, then remove it."""
    marker = os.path.join(os.path.dirname(__file__), ".one_time_cleanup")
    if not os.path.exists(marker):
        return
    with open(marker) as f:
        args = f.read().strip().split()
    os.remove(marker)
    print("=" * 50)
    print("One-time cleanup")
    print("=" * 50)
    import cleanup_orphaned_blocks
    # Inject args for the cleanup script
    import sys
    old_argv = sys.argv
    sys.argv = ["cleanup_orphaned_blocks.py", "--delete"] + args
    try:
        cleanup_orphaned_blocks.main()
    finally:
        sys.argv = old_argv
    print()


def main():
    _run_one_time_cleanup()

    print("=" * 50)
    print("Step 1: Shift buffer sync")
    print("=" * 50)
    shift_buffers.run()

    print()
    print("=" * 50)
    print("Step 2: Google Calendar -> DrChrono sync")
    print("=" * 50)
    conflicts = sync.sync()

    print()
    print("=" * 50)
    print("Step 3: DrChrono -> Google Calendar filtered sync")
    print("=" * 50)
    drchrono_to_gcal.run()

    # Send email if any blocks failed due to conflicts
    if conflicts:
        print()
        print("=" * 50)
        print("Sending conflict notification...")
        print("=" * 50)
        try:
            notify.send_conflict_email(conflicts)
        except Exception as e:
            print(f"WARNING: Failed to send notification: {e}")


if __name__ == "__main__":
    main()
