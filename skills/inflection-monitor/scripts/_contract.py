#!/usr/bin/env python3
"""The error contract every entry point honours.

On failure a script prints ONE JSON envelope to stdout -

    {"status": "error", "stage": "<script or subcommand>", "message": "..."}

- writes the message (and, for an unexpected exception, the traceback) to
stderr, and exits non-zero. A documented capture such as
`compute.py chat ... > out/chat.txt` or `ground_truth.py ... > out/gt.json`
therefore never produces an empty file that a later step reads as a pass.

A script that has already printed its own result and exits non-zero on
purpose (verify's fatal list, a failed lint run) is left alone: its exit code
is an integer and its stdout already says why.
"""

import json
import sys
import traceback


def envelope(stage, message):
    print(json.dumps({"status": "error", "stage": stage, "message": str(message)}, indent=2))
    sys.stdout.flush()


def run(main, stage):
    try:
        rc = main()
    except SystemExit as e:
        code = e.code
        if code is None or code == 0:
            raise
        if isinstance(code, int):
            if code == 2 and "--help" not in sys.argv and "-h" not in sys.argv:
                envelope(stage, "invalid arguments (see stderr for usage)")
            raise
        envelope(stage, code)
        sys.stderr.write(str(code).rstrip() + "\n")
        sys.exit(1)
    except KeyboardInterrupt:
        envelope(stage, "interrupted")
        sys.exit(130)
    except Exception as e:
        envelope(stage, "%s: %s" % (type(e).__name__, e))
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
    if isinstance(rc, int) and rc:
        sys.exit(rc)
