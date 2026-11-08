"""pys-sessions: keep a record of every pys window (folder, title, the Claude Code session
running in it, its last screen) outside pys, and open them again after pys died.

  pys-sessions save [--quiet]        snapshot now (ON_CHANGE and the timer run this)
  pys-sessions list                  the snapshots, newest last
  pys-sessions show [SNAP]           what a snapshot holds (default: the last one of the
                                     previous pys server)
  pys-sessions restore [SNAP] [--only W,W] [--yes]
                                     open its windows again: `claude <its flags> --resume`
                                     or a shell in the same folder, with the same title; a
                                     dry run without --yes
  pys-sessions forensic [--write]    no snapshot? rebuild the last dead pys server's windows
                                     from its server record (windows, folders, titles) and,
                                     for the Claude sessions, from server.log, Claude's
                                     leftover sockets and the transcripts (see `why`)
  pys-sessions install               a systemd user timer that runs `save` every 2 minutes

Snapshots: $PYS_SESSIONS_DIR, default ~/.local/state/pys-sessions/<YYYYMMDD-HHMMSS>/
(windows.json + one gzipped screen per window). Never deleted or overwritten: a new one is
written when the set of windows changes, or every 2 hours to refresh the screens.

In ~/.config/pys/config.py:
  ON_CHANGE = ["pys-sessions", "save", "--quiet"]   # a snapshot at each change and a kill
  SESSIONS_KEEP_ENV = ["MY_VAR"]   # variables of a claude's environment its resume gets back

Codex sessions are saved and resumed too (`codex resume`), untested: Claude Code is the
supported agent for now.

Agent skill: `pys-restore` teaches an agent this whole flow (save, show, the dry run, restore);
`pys-agents skills --install` links it, with `pys-drive`, into ~/.claude/skills.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from .. import config, proto
from . import claude, gitinfo, procs
from .procs import pys

HOME = Path.home()
REFRESH = 2 * 3600          # seconds: a new snapshot at least this often, for the screens
HIST_LINES = 3000           # scrollback lines kept per window


def state_dir() -> Path:
    return Path(os.environ.get("PYS_SESSIONS_DIR") or HOME / ".local/state/pys-sessions")


def records_dir() -> Path:
    """Where the brain writes its server records (brain.record_path)."""
    base = os.environ.get("XDG_STATE_HOME") or HOME / ".local/state"
    return Path(base) / "pys" / "servers"


def keep_env() -> list[str]:
    try:
        return config.load_config().sessions_keep_env
    except Exception:            # a broken config must not stop a save
        return []


# flags of the original claude that a resume keeps (value-taking ones take the next word)
KEEP_VAL = {"--name", "-n", "--permission-mode", "--model", "--add-dir", "--agent", "--settings",
            "--mcp-config", "--allowedTools", "--allowed-tools", "--disallowedTools",
            "--disallowed-tools", "--effort", "--fallback-model"}
KEEP_BOOL = {"--dangerously-skip-permissions", "--chrome", "--ide", "--verbose"}


def keep_flags(argv: list[str]) -> list[str]:
    out, i = [], 1
    while i < len(argv):
        a = argv[i]
        key = a.split("=", 1)[0]
        if key in KEEP_BOOL:
            out.append(a)
        elif key in KEEP_VAL:
            if "=" in a:
                out.append(a)
            elif i + 1 < len(argv):
                out += [a, argv[i + 1]]
                i += 1
        i += 1
    return out


def claude_session(pid: int, env_keys: list[str]) -> dict | None:
    d = claude.session_of(pid)
    if d is None:
        return None
    return {"kind": "claude", "pid": pid, "session": d.get("sessionId"), "cwd": d.get("cwd"),
            "name": d.get("name"), "argv": procs.cmdline(pid),
            "env": procs.environ(pid, env_keys)}


ROLL = re.compile(r"rollout-.*-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$")


def codex_session(pid: int) -> dict | None:
    """Untested path: the rollout file a running codex holds open names its session."""
    try:
        fds = os.listdir(f"/proc/{pid}/fd")
    except OSError:
        return None
    for fd in fds:
        try:
            m = ROLL.search(os.readlink(f"/proc/{pid}/fd/{fd}"))
        except OSError:
            continue
        if m:
            return {"kind": "codex", "pid": pid, "session": m[1], "cwd": procs.cwd(pid)}
    return None


def agent_in(shell_pid: int, kids, env_keys: list[str]) -> tuple[dict | None, list[str]]:
    """The agent session running under a window's shell, and the foreground command (the
    shell's first child) for a plain shell."""
    fg: list[str] = []
    # the window's own process first: `pys new claude` (or `bash -c "claude …"`, which
    # execs it) runs the agent as the window itself, with no shell above it
    for p in [shell_pid, *procs.descendants(shell_pid, kids)]:
        if not fg and p != shell_pid:
            fg = procs.cmdline(p)
        # a claude is a pid with its own session file (what Claude Code writes, and what
        # pys-agents goes by): no guess from the process name, a wrapper is found too
        if s := claude_session(p, env_keys):
            return s, fg
        if procs.comm(p).startswith("codex"):
            if s := codex_session(p):
                return s, fg
    return None, fg


# ---- save -----------------------------------------------------------------------
def snapshots() -> list[Path]:
    return sorted((p for p in state_dir().glob("2*") if (p / "windows.json").exists()),
                  key=lambda p: (p.name[:15], p.stat().st_mtime))


def new_dir(name: str) -> Path:
    """A snapshot dir that never replaces one: a second in the same second gets .v2, .v3."""
    d, n = state_dir() / name, 1
    while d.exists():
        n += 1
        d = state_dir() / f"{name}.v{n}"
    d.mkdir(parents=True)
    return d


def cmd_save(a) -> int:
    try:
        info = json.loads(pys("ls", "--json"))
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as e:
        if not a.quiet:
            print(f"pys-sessions: no pys server ({e})", file=sys.stderr)
        return 0
    kids, keys = procs.children_map(), keep_env()
    wins = []
    for w in info["windows"]:
        agent, fg = agent_in(w["pid"], kids, keys)
        cwd = procs.cwd(w["pid"]) or w.get("cwd")
        wins.append({
            "wid": w["wid"], "name": w.get("name"), "title": w.get("title"),
            "agent_title": w.get("agent_title"), "cmd": w.get("cmd"),
            "cwd": cwd, "shell_pid": w["pid"], "foreground": fg, "agent": agent,
        })
        w_ = wins[-1]
        # where the work was: repo, branch, HEAD, worktree (cheap: no `git status`, so it fits
        # in the kill hook). For an agent, the repo of its latest writes (its transcript): it
        # resumes in the folder it started in, but that is not always where it worked
        if agent and agent["kind"] == "claude" and (pl := claude.works_in(
                {"sessionId": agent.get("session"), "cwd": agent.get("cwd")})):
            w_["works_in"], w_["git"] = pl["top"], pl
        if "git" not in w_:
            w_["git"] = gitinfo.place((agent or {}).get("cwd") or cwd)
    sig_src = [(w["wid"], w["cwd"], w["name"], (w["agent"] or {}).get("session"),
                (w["git"] or {}).get("branch")) for w in wins]
    sig = hashlib.sha256(json.dumps([info["pid"], sig_src]).encode()).hexdigest()[:16]
    snaps = snapshots()
    if snaps:
        last = json.loads((snaps[-1] / "windows.json").read_text())
        age = time.time() - last.get("saved_at", 0)
        # PYS_EVENT=kill: pys's ON_CHANGE hook, run just before the windows end: always keep it
        if last.get("sig") == sig and age < REFRESH and os.environ.get("PYS_EVENT") != "kill":
            if not a.quiet:
                print(f"unchanged since {snaps[-1].name}")
            return 0
    d = new_dir(datetime.now().strftime("%Y%m%d-%H%M%S"))
    for w in wins:
        try:
            text = pys("capture", "--history", str(w["wid"]))
        except (RuntimeError, subprocess.TimeoutExpired):
            continue
        text = "\n".join(text.splitlines()[-HIST_LINES:]) + "\n"
        with gzip.open(d / f"screen-{w['wid']}.txt.gz", "wt") as f:
            f.write(text)
        w["screen"] = f"screen-{w['wid']}.txt.gz"
    snap = {"saved_at": time.time(), "saved": d.name, "server_pid": info["pid"], "sig": sig,
            "source": "save", "event": os.environ.get("PYS_EVENT"), "layout": info.get("panes"),
            "windows": wins}
    (d / "windows.json").write_text(json.dumps(snap, indent=1, ensure_ascii=False))
    if not a.quiet:
        print(f"saved {d} ({len(wins)} windows)")
    return 0


# ---- list / show ----------------------------------------------------------------
def current_server_pid() -> int | None:
    try:
        return json.loads(pys("ls", "--json"))["pid"]
    except Exception:
        return None


def pick(spec: str | None) -> Path:
    snaps = snapshots()
    if spec:
        cand = [s for s in snaps if s.name == spec or s.name.startswith(spec)]
        if not cand:
            sys.exit(f"pys-sessions: no snapshot {spec!r} (see `pys-sessions list`)")
        return cand[-1]
    if not snaps:
        sys.exit("pys-sessions: no snapshot yet; try `pys-sessions forensic --write`")
    now = current_server_pid()
    for s in reversed(snaps):              # the last one of an earlier pys server
        if json.loads((s / "windows.json").read_text()).get("server_pid") != now:
            return s
    return snaps[-1]


def label(w: dict) -> str:
    return w.get("agent_title") or w.get("title") or w.get("name") or ""


def describe(w: dict) -> str:
    ag = w.get("agent")
    if ag:
        return f"{ag['kind']} {ag['session']}"
    if e := w.get("ended_agent"):
        return f"shell (its {e['kind']} {e['session'][:8]} had ended, last {e['last']})"
    fg = w.get("foreground") or []
    return "shell" + (f" (was running: {shlex.join(fg)[:60]})" if fg else "")


def cmd_list(a) -> int:
    now = current_server_pid()
    for s in snapshots():
        d = json.loads((s / "windows.json").read_text())
        n = len(d["windows"])
        ag = sum(1 for w in d["windows"] if w.get("agent"))
        mark = "  (this server)" if d.get("server_pid") == now else ""
        print(f"{s.name}  {d.get('source', 'save'):<8} {n:>2} windows, {ag} agents{mark}")
    return 0


def cmd_show(a) -> int:
    s = pick(a.snap)
    d = json.loads((s / "windows.json").read_text())
    print(f"snapshot {s}  (server pid {d.get('server_pid')}, {d.get('source', 'save')})")
    for w in d["windows"]:
        print(f"  win {w['wid']:>2}  {label(w)[:26]:<26} {w.get('cwd') or '?'}")
        print(f"          {describe(w)}")
        if g := gitinfo.describe(w.get("git")):
            print(f"          git: {g}")
        if gone := folder_gone(w):
            print(f"          ⚠ {gone}")
        if w.get("why"):
            print(f"          why: {w['why']}")
        if w.get("screen"):
            print(f"          screen: {s / w['screen']}")
    return 0


# ---- restore --------------------------------------------------------------------
def live_agent_sessions() -> set[str]:
    return {d["sessionId"] for d in claude.live_sessions().values()}


def folder_gone(w: dict) -> str:
    """A warning when the window's folder no longer exists (a worktree removed since): the
    window then opens in ~, and for a worktree we say how to bring the folder back."""
    start = (w.get("agent") or {}).get("cwd") or w.get("cwd")
    cwd = next((d for d in (start, w.get("works_in")) if d and not os.path.isdir(d)), None)
    if not cwd:
        return ""
    msg = f"folder gone: {cwd}" + (" (opens in ~)" if cwd == start else " (where it worked)")
    g = w.get("git") or {}
    if g.get("worktree") and g.get("branch") and g.get("common"):
        msg += (f"; to bring it back: git --git-dir={shlex.quote(g['common'])} worktree add "
                f"{shlex.quote(cwd)} {shlex.quote(g['branch'])}")
    return msg


def window_cmd(w: dict, snapdir: Path) -> str:
    cwd = w.get("cwd") or str(HOME)
    ag = w.get("agent")
    banner = f"restored by pys-sessions from {snapdir.name}, window {w['wid']}"
    if w.get("screen"):
        banner += f"; its last screen: zless {snapdir / w['screen']}"
    parts = [f"cd {shlex.quote(cwd)} || cd", f"printf '%s\\n' {shlex.quote(banner)}"]
    if ag and ag["kind"] == "claude":
        env = "".join(f"{k}={shlex.quote(v)} " for k, v in (ag.get("env") or {}).items())
        flags = shlex.join(keep_flags(ag.get("argv") or ["claude"]))
        parts.append(f"{env}claude {flags + ' ' if flags else ''}--resume {shlex.quote(ag['session'])}")
    elif ag and ag["kind"] == "codex":
        parts.append(f"codex resume {shlex.quote(ag['session'])}")
    elif (e := w.get("ended_agent")):
        hint = f"{e['kind']} {e['session']} ran here and ended; to go on: claude --resume {e['session']}"
        parts.append(f"printf '%s\\n' {shlex.quote(hint)}")
    elif w.get("foreground"):
        parts.append(f"printf '%s\\n' {shlex.quote('it was running: ' + shlex.join(w['foreground']))}")
    parts.append("exec bash -i")
    return "; ".join(parts)


def titled_by_agent(wid: int) -> bool:
    try:
        ws = json.loads(pys("ls", "--json"))["windows"]
    except (RuntimeError, ValueError):
        return False
    return any(x["wid"] == wid and x.get("agent_title") for x in ws)


def cmd_restore(a) -> int:
    s = pick(a.snap)
    d = json.loads((s / "windows.json").read_text())
    only = {int(x) for x in a.only.split(",")} if a.only else None
    live = live_agent_sessions()
    plan = []
    for w in d["windows"]:
        if only is not None and w["wid"] not in only:
            continue
        ag = w.get("agent")
        if ag and ag["session"] in live:
            print(f"  skip win {w['wid']}: {ag['kind']} {ag['session'][:8]} is already running")
            continue
        plan.append(w)
    print(f"restore from {s}:")
    for w in plan:
        print(f"  win {w['wid']:>2} → new window  {label(w)[:26]:<26} {w.get('cwd')}  [{describe(w)}]")
        if g := gitinfo.describe(w.get("git")):
            print(f"           {g}")
        if gone := folder_gone(w):
            print(f"           ⚠ {gone}")
    if not a.yes:
        print("dry run; add --yes to open them")
        return 0
    for w in plan:
        try:
            before = {x["wid"] for x in json.loads(pys("ls", "--json"))["windows"]}
        except (RuntimeError, ValueError):
            before = set()             # no server yet: the first `pys new` starts it
        pys("new", "bash", "-c", window_cmd(w, s))
        time.sleep(0.3)
        after = json.loads(pys("ls", "--json"))["windows"]
        new = [x for x in after if x["wid"] not in before]
        t = label(w)
        if new and t and not titled_by_agent(new[0]["wid"]):
            # owned by the new window's shell: it lasts while the window does, and the
            # resumed agent replaces it with its own title when it sets one. Not when the
            # agent was faster and named the window already: its own title is the truth
            pys("title", "--wid", str(new[0]["wid"]), "--owner", str(new[0]["pid"]),
                f"↺{t}"[:28], check=False)
        print(f"  opened win {new[0]['wid'] if new else '?'} for old win {w['wid']} ({t})")
    return 0


# ---- forensic -------------------------------------------------------------------
LOG = re.compile(r"^(\d\d:\d\d:\d\d) (\w+): (.*)$")


def dated_log(path: Path) -> list[tuple[datetime, str, str]]:
    """server.log has times only; date them backwards from the file's mtime."""
    lines = [m.groups() for l in path.read_text(errors="replace").splitlines() if (m := LOG.match(l))]
    day = datetime.fromtimestamp(path.stat().st_mtime).date()
    out, prev = [], None
    for t, who, msg in reversed(lines):
        tt = datetime.strptime(t, "%H:%M:%S").time()
        if prev is not None and tt > prev:
            day -= timedelta(days=1)
        prev = tt
        out.append((datetime.combine(day, tt), who, msg))
    return list(reversed(out))


def server_record(keeper_pid: int | None) -> dict | None:
    """The brain's record of the server whose keeper had this pid (pys since 665553a):
    exact windows, folders and titles, where the log only has numbers."""
    if keeper_pid is None:
        return None
    for f in sorted(records_dir().glob(f"*-{keeper_pid}.json"), reverse=True):
        try:
            return json.loads(f.read_text()) | {"path": str(f)}
        except (OSError, ValueError):
            continue
    return None


META: dict[str, dict] = {}      # sid -> last permissionMode / customTitle seen in it


def transcripts_since(since: datetime):
    """cli transcripts touched since `since`: id -> (path, cwd, [timestamps], last title)."""
    out = {}
    cutoff = since.timestamp()
    for f in (claude.home() / "projects").glob("*/*.jsonl"):
        if f.stat().st_mtime < cutoff:
            continue
        cwd, ent, ts, title, meta = None, None, [], None, {}
        with open(f, errors="replace") as fh:
            for l in fh:
                try:
                    j = json.loads(l)
                except ValueError:
                    continue
                cwd = cwd or j.get("cwd")
                if j.get("permissionMode"):
                    meta["mode"] = j["permissionMode"]
                if j.get("type") == "custom-title" and j.get("customTitle"):
                    meta["name"] = j["customTitle"]
                ent = ent or j.get("entrypoint")
                if t := j.get("timestamp"):
                    ts.append(datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone().replace(tzinfo=None))
                if "pys title --owner" in l:
                    for m in re.finditer(r'pys title --owner \$PPID \\?"([^"\\$<]+)\\?"', l):
                        title = m[1]
        if ent == "cli" and ts:
            out[f.stem] = (f, cwd, ts, title)
            META[f.stem] = meta
    return out


def cmd_forensic(a) -> int:
    rd = proto.runtime_dir()
    log = dated_log(rd / "server.log")
    starts = [i for i, (_, who, msg) in enumerate(log) if who == "keeper" and msg.startswith("listening")]
    if len(starts) < 2:
        sys.exit("pys-sessions: server.log holds no earlier pys server")
    seg = log[starts[-2]:starts[-1]]
    began, ended = seg[0][0], log[starts[-1]][0]
    m = re.search(r" pid (\d+)", seg[0][2])
    rec = server_record(int(m[1]) if m else None)
    spawned, live = {}, {}
    for when, who, msg in seg:
        if m := re.match(r"spawned (\d+) (\d+) (.*)", msg):
            spawned[int(m[1])] = (when, int(m[2]))
            live[int(m[1])] = (when, int(m[2]))
        elif m := re.match(r"exited (\d+)", msg):
            w = int(m[1])
            if w in live:
                spawned[w] = (*spawned[w], when)
                live.pop(w)

    def alive_at(w, t):
        s = spawned[w]
        return s[0] <= t and (len(s) < 3 or s[2] > t)

    # Claude leaves its messaging socket behind when it is killed: <pid>.sock, made at start
    socks = []
    for sk in (Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "cc-socks").glob("*.sock"):
        try:
            pid = int(sk.stem)
        except ValueError:
            continue
        started = datetime.fromtimestamp(sk.stat().st_mtime)
        if began <= started < ended and not Path(f"/proc/{pid}").exists():
            socks.append((pid, started))
    trs = transcripts_since(began)
    running = live_agent_sessions()   # never this session or another live one
    used = set()
    wins = {w: {"wid": w, "cmd": ["bash"], "cwd": None, "agent": None, "agent_title": None,
                "why": f"shell pid {v[1]}, opened {v[0]:%m-%d %H:%M}, open when pys died"}
            for w, v in live.items()}
    free = {sid: v for sid, v in trs.items() if sid not in running}
    NEW = timedelta(minutes=5)
    LEAD = timedelta(seconds=60)   # a session started with a prompt writes before its socket

    def new_after(t0, exclude):
        """Transcripts born from 1 min before to 5 min after t0, nearest first."""
        c = [(abs(v[2][0] - t0), sid) for sid, v in free.items()
             if sid not in exclude and -LEAD <= v[2][0] - t0 < NEW]
        return sorted(c)

    def window_of(pid, started):
        """the newest window open then whose shell is older than this claude"""
        cands = [w for w in spawned if alive_at(w, started) and spawned[w][1] < pid]
        return max(cands, key=lambda x: spawned[x][1]) if cands else None

    def argv_of(pid):
        """a ps listing in some transcript may show how this claude was started"""
        pat = re.compile(rf"(?<!\d){pid} (claude [^\\\n\"]*)")
        hits = [m[1] for f, *_ in trs.values() for m in pat.finditer(f.read_text(errors="replace"))]
        return max(hits, key=len).split() if hits else ["claude"]   # the least cut-off one

    def with_meta(argv, meta):
        """no ps line? the transcript still knows its permission mode and name"""
        flags = keep_flags(argv)
        if "--permission-mode" not in flags and meta.get("mode") and meta["mode"] != "default":
            argv = argv + ["--permission-mode", meta["mode"]]
        if not {"--name", "-n"} & set(flags) and meta.get("name"):
            argv = argv + ["--name", meta["name"]]
        return argv

    def put(w, pid, sid, why):
        used.add(sid)
        if w in live:
            f, cwd, ts, title = trs[sid]
            wins[w].update(cwd=cwd, agent_title=title,
                           agent={"kind": "claude", "pid": pid, "session": sid, "cwd": cwd,
                                  "argv": with_meta(argv_of(pid), META.get(sid, {}))})
            wins[w]["why"] += f"; {why}; last entry {ts[-1]:%m-%d %H:%M}"

    # pass 1: a killed claude whose transcript was born just after it started (in any
    # window, closed ones too, so they claim theirs)
    pending = []
    for pid, started in sorted(socks, key=lambda x: x[1]):
        w = window_of(pid, started)
        if w is None:
            continue
        if c := new_after(started, used):
            gap, sid = c[0]
            put(w, pid, sid, f"its transcript began {int(gap.total_seconds())}s after claude pid {pid} "
                f"started {started:%H:%M:%S}" + (f" (nearest of {len(c)})" if len(c) > 1 else ""))
        else:
            pending.append((pid, started, w))
    # pass 2: what each window ran first: a transcript born just after the window opened
    first_of = {}
    for w, s in sorted(spawned.items(), key=lambda x: x[1][0]):
        if c := new_after(s[0], used | set(first_of.values())):
            first_of[w] = c[0][1]
    # pass 3: a killed claude with no new transcript resumed the one its window ran
    for pid, started, w in pending:
        prev = first_of.get(w)
        if prev and prev not in used and \
                any(abs(t - started) < timedelta(minutes=2) for t in free[prev][2]):
            put(w, pid, prev, f"claude pid {pid} started {started:%H:%M:%S} in the window that ran "
                f"this session, which goes on then (a resume)")
        elif w in live:
            wins[w]["why"] += f"; claude pid {pid} was killed here, its transcript not found"

    # a window with no killed claude: what it ran first gives the folder; that claude
    # ended by itself before pys died, so restore opens a shell and says how to go on
    for w, v in wins.items():
        if v["agent"] is None and (sid := first_of.get(w)) and sid not in used:
            f, cwd, ts, title = trs[sid]
            v.update(cwd=cwd, agent_title=title,
                     ended_agent={"kind": "claude", "session": sid, "last": f"{ts[-1]:%m-%d %H:%M}"})
            v["why"] += f"; claude {sid[:8]} began here {ts[0]:%H:%M} and ended by itself (no socket left), last entry {ts[-1]:%m-%d %H:%M}"

    # the server record, when pys wrote one, knows each window's folder and titles for sure:
    # it wins over the guesses from the transcripts
    if rec:
        for rw in rec.get("windows", []):
            v = wins.get(rw.get("wid"))
            if v is None:
                continue
            for k in ("cwd", "name", "title", "agent_title"):
                if rw.get(k):
                    v[k] = rw[k]
            v["why"] += "; folder and titles from the server record"
    snap = {"saved_at": time.time(), "saved": f"{began:%Y%m%d-%H%M%S}-forensic", "server_pid": None,
            "sig": None, "source": "forensic", "began": str(began), "ended_before": str(ended),
            "record": (rec or {}).get("path"), "windows": [wins[w] for w in sorted(wins)]}
    print(f"pys server {began:%m-%d %H:%M} → died before {ended:%m-%d %H:%M}: {len(wins)} windows open"
          + ("" if rec else " (no server record: folders are guessed)"))
    for w in snap["windows"]:
        print(f"  win {w['wid']:>2}  {label(w)[:26]:<26} {w['cwd'] or '(folder unknown)'}")
        print(f"          {describe(w)}\n          why: {w['why']}")
    if a.write:
        d = new_dir(snap["saved"])
        snap["saved"] = d.name
        (d / "windows.json").write_text(json.dumps(snap, indent=1, ensure_ascii=False))
        print(f"written {d}; `pys-sessions restore {d.name}` to open it")
    return 0


# ---- install --------------------------------------------------------------------
def cmd_install(a) -> int:
    ud = HOME / ".config/systemd/user"
    ud.mkdir(parents=True, exist_ok=True)
    exe = shutil.which("pys-sessions") or str(Path(sys.argv[0]).resolve())
    (ud / "pys-sessions.service").write_text(
        "[Unit]\nDescription=pys-sessions: snapshot the pys windows\n\n"
        f"[Service]\nType=oneshot\nExecStart={exe} save --quiet\n")
    (ud / "pys-sessions.timer").write_text(
        "[Unit]\nDescription=pys-sessions every 2 minutes\n\n"
        "[Timer]\nOnBootSec=1min\nOnUnitActiveSec=2min\nAccuracySec=15s\n\n"
        "[Install]\nWantedBy=timers.target\n")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "pys-sessions.timer"], check=True)
    print("installed: systemctl --user list-timers pys-sessions.timer")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pys-sessions", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("save"); p.add_argument("--quiet", action="store_true"); p.set_defaults(f=cmd_save)
    sp.add_parser("list").set_defaults(f=cmd_list)
    p = sp.add_parser("show"); p.add_argument("snap", nargs="?"); p.set_defaults(f=cmd_show)
    p = sp.add_parser("restore"); p.add_argument("snap", nargs="?"); p.add_argument("--only")
    p.add_argument("--yes", action="store_true"); p.set_defaults(f=cmd_restore)
    p = sp.add_parser("forensic"); p.add_argument("--write", action="store_true"); p.set_defaults(f=cmd_forensic)
    sp.add_parser("install").set_defaults(f=cmd_install)
    a = ap.parse_args(argv)
    return a.f(a)


if __name__ == "__main__":
    sys.exit(main())
