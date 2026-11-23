"""Wall-clock limits for calls that can hang (an engine, the judge, Langfuse)."""

from __future__ import annotations

import threading


class Deadline(Exception):
    """`with_deadline` stopped waiting."""


def with_deadline(fn, seconds: float):
    """Return fn(), but wait at most `seconds` (wall clock); else raise Deadline.

    fn runs in a daemon thread: if it hangs, the thread stays, but the case goes on.
    """
    box: dict = {}

    def work():
        try:
            box["value"] = fn()
        except BaseException as e:
            box["error"] = e

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise Deadline(f"no answer in {seconds:.0f} s")
    if "error" in box:
        raise box["error"]
    return box["value"]
