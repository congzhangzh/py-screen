"""M1 flood fix: a flooding window must not starve typing, and the screen a person
sees after a flood (visible or hidden) must still be right."""

from __future__ import annotations

import json
import statistics
import time

import pytest

from harness import PYS, Session, Term

C_RB = "\x1d"
FLOOD = "yes 'flood line: the quick brown fox jumps over the lazy dog'"


@pytest.fixture
def sess():
    s = Session("f")
    yield s
    s.kill()


def attach(s: Session) -> Term:
    t = Term(PYS + ["attach"], s.env)
    t.wait_for(" pys ")
    t.wait_for("$ ")
    return t


def echo_times(t: Term, n: int) -> list[float]:
    """Type a marker + Enter into `cat`; seconds until it first comes back."""
    out = []
    for i in range(n):
        marker = f"m{i:03d}z".encode()
        hit: dict = {}

        def on_data(data: bytes, ts: float, marker=marker, hit=hit) -> None:
            hit["buf"] = hit.get("buf", b"")[-16:] + data
            if "t" not in hit and marker in hit["buf"]:
                hit["t"] = ts
        with t.lock:
            t.on_data = on_data
        t0 = time.monotonic()
        t.send(marker + b"\r")
        while "t" not in hit and time.monotonic() - t0 < 3:
            time.sleep(0.0005)
        with t.lock:
            t.on_data = None
        out.append(hit.get("t", t0 + 3) - t0)
        time.sleep(0.03)
    return out


def test_typing_while_hidden_window_floods(sess):
    t = attach(sess)
    sess.pys("new", "--", "sh", "-c", FLOOD)          # window 1, hidden, floods forever
    time.sleep(1.0)
    t.send("cat\r")
    time.sleep(0.3)
    times = sorted(echo_times(t, 30))
    med, worst = statistics.median(times), times[-1]
    print(f"\necho during hidden flood: median {med * 1e3:.1f} ms, max {worst * 1e3:.1f} ms")
    assert worst < 1.0, times                          # M0: every sample hit the 5 s cap
    assert med < 0.05, times

    t.send(C_RB + "1")                                 # show the flood: it must appear
    t.wait_for("flood line: the quick brown fox", timeout=3)
    t.send(C_RB + "0")
    t.wait_for("m029z", timeout=3)
    t.close()


def test_visible_flood_ends_on_the_right_screen(sess):
    t = attach(sess)
    t0 = time.monotonic()
    t.send("seq -f %.0f 1 3000000; echo END-$((1+1))\r")   # -f: BSD seq prints 3e+06
    t.wait_for("END-2", timeout=120)
    took = time.monotonic() - t0
    lines = [line.rstrip() for line in t.text().splitlines()]
    end = next(i for i, line in enumerate(lines) if line == "END-2")
    assert lines[end - 1] == "3000000", lines[end - 3:end + 1]
    assert lines[end - 2] == "2999999"
    skipped = json.loads(sess.pys("ls", "--json"))["windows"][0]["skipped"]
    print(f"\nvisible seq 1..3000000: {took:.1f} s, {skipped} bytes never parsed")
    t.close()


def test_hidden_flood_shows_its_end_when_selected(sess):
    t = attach(sess)
    done = sess.dir / "hidden.done"
    sess.pys("new", "--", "sh", "-c",
             f"seq -f %.0f 1 2000000; echo HIDDEN-DONE; touch {done}; exec sleep 600")
    deadline = time.monotonic() + 120                  # slow CI runners: wait for the fact
    while not done.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    assert done.exists(), "hidden seq did not finish"
    time.sleep(0.5)
    w = json.loads(sess.pys("ls", "--json"))["windows"][1]
    assert w["skipped"] > 1_000_000, w                 # the hidden flood really was trimmed
    t.send(C_RB + "1")
    t.wait_for("HIDDEN-DONE", timeout=5)
    lines = [line.rstrip() for line in t.text().splitlines()]
    i = lines.index("HIDDEN-DONE")
    assert lines[i - 1] == "2000000", lines[i - 3:i + 1]
    t.close()
