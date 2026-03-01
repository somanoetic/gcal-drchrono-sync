"""Run shift buffer sync then DrChrono sync in sequence.

Single entry point for Task Scheduler.

Usage:
    python run_all.py          # normal run
    python run_all.py --full   # force full re-sync for both steps
"""

import sys

import shift_buffers
import sync
import drchrono_to_gcal


def main():
    print("=" * 50)
    print("Step 1: Shift buffer sync")
    print("=" * 50)
    shift_buffers.run()

    print()
    print("=" * 50)
    print("Step 2: Google Calendar → DrChrono sync")
    print("=" * 50)
    sync.sync()

    print()
    print("=" * 50)
    print("Step 3: DrChrono → Google Calendar filtered sync")
    print("=" * 50)
    drchrono_to_gcal.run()


if __name__ == "__main__":
    main()
