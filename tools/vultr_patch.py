#!/usr/bin/env python3
"""
One-off ops tool: patch /root/ai-check/app.py on the Vultr box for SQLite
concurrency safety (busy timeout + WAL journal mode).

This is a LOCAL-ONLY tool. It never installs anything on the server and never
prints or logs any secret value. The only remote changes it makes are:
  * /root/ai-check/app.py        (patched in place)
  * /root/ai-check/app.py.bak    (one-time backup, created with `cp -n`)

Run with the project venv:
    .venv\\Scripts\\python.exe tools\\vultr_patch.py

Patch-only mode (never restarts/kills/signals anything; the human restarts
the process by hand afterwards):
    .venv\\Scripts\\python.exe tools\\vultr_patch.py --no-restart

Budget-fence mode (adds the persistent daily budget fence; PATCH-ONLY, always,
regardless of any other flag -- it never restarts/kills/signals anything):
    .venv\\Scripts\\python.exe tools\\vultr_patch.py --budget-patch

    It fetches the live /root/ai-check/app.py, validates the four existing
    patch anchors, builds a candidate in memory, runs local marker + py_compile
    + stdlib logic checks, then makes ONE new backup (app.py.bak-budget),
    uploads the candidate, and verifies md5 / remote py_compile / GET /health.
"""

import hashlib
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time

import paramiko
import requests

# Patch-only mode: skip ALL restart logic (systemctl restart, rollback-restart,
# service status checks). The running process is left completely untouched.
NO_RESTART = "--no-restart" in sys.argv

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(REPO_ROOT, ".env")
if not os.path.isfile(ENV_PATH) and os.path.isfile(os.path.join(REPO_ROOT, "env")):
    ENV_PATH = os.path.join(REPO_ROOT, "env")

REMOTE_PATH = "/root/ai-check/app.py"
REMOTE_BAK = "/root/ai-check/app.py.bak"
REMOTE_BAK_BUDGET = "/root/ai-check/app.py.bak-budget"
SERVICE = "ai-check"

# Marker comments already present in the live file (validated before patching).
IMAGE_SAFETY_MARK = "# image-safety: chunk failures tolerated"
CACHE_POLICY_MARK = "# cache-policy: inconclusive results are never cached"

CONNECT_OLD = "conn = sqlite3.connect(DB_PATH)"
CONNECT_NEW = "conn = sqlite3.connect(DB_PATH, timeout=30)"
PRAGMA_LINE = 'conn.execute("PRAGMA journal_mode=WAL")'

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ENV = 2
EXIT_PRECHECK = 3
EXIT_VALIDATE = 4


def log(msg):
    print(msg, flush=True)


# --------------------------------------------------------------------------
# .env loading (tolerant)
# --------------------------------------------------------------------------
def load_env(path):
    values = {}
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            values[key] = val
    return values


def get_secret(env, key):
    """Return the value, or None if missing/empty/placeholder."""
    val = env.get(key, "")
    if not val:
        return None
    if "PUT " in val:
        return None
    return val


# --------------------------------------------------------------------------
# SSH / SFTP helpers
# --------------------------------------------------------------------------
def ssh_connect(host, user, password):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        username=user,
        password=password,
        timeout=20,
        banner_timeout=20,
        auth_timeout=20,
        look_for_keys=False,
        allow_agent=False,
    )
    return client


def ssh_run(client, command):
    stdin, stdout, stderr = client.exec_command(command, timeout=30)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    return code, out, err


def sftp_read(client, path):
    sftp = client.open_sftp()
    try:
        with sftp.open(path, "r") as fh:
            return fh.read().decode("utf-8")
    finally:
        sftp.close()


def sftp_read_bytes(client, path):
    sftp = client.open_sftp()
    try:
        with sftp.open(path, "r") as fh:
            return fh.read()
    finally:
        sftp.close()


def sftp_write(client, path, text):
    sftp = client.open_sftp()
    try:
        with sftp.open(path, "w") as fh:
            fh.write(text.encode("utf-8"))
        sftp.chmod(path, 0o644)
    finally:
        sftp.close()


# --------------------------------------------------------------------------
# HTTP checks
# --------------------------------------------------------------------------
def health_check(host):
    resp = requests.get("http://%s/health" % host, timeout=10)
    return resp.status_code, resp.json()


def auth_check(host):
    resp = requests.post(
        "http://%s/check" % host,
        headers={"X-API-Key": "wrong-key-purposely"},
        timeout=10,
    )
    return resp.status_code, resp.text


# --------------------------------------------------------------------------
# Code analysis / patching
# --------------------------------------------------------------------------
def find_init_db_start(lines):
    for i, ln in enumerate(lines):
        if re.match(r"def init_db\s*\(", ln):
            return i
    return None


def init_db_block(content):
    lines = content.split("\n")
    start = find_init_db_start(lines)
    if start is None:
        return None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"def \w+\s*\(", lines[i]):
            end = i
            break
    return "\n".join(lines[start:end])


def apply_patch(content):
    """Return (patched_content, inserted_pragma_line_with_indent)."""
    patched = content.replace(CONNECT_OLD, CONNECT_NEW)
    lines = patched.split("\n")
    start = find_init_db_start(lines)
    if start is None:
        raise ValueError("init_db() not found while patching")
    close_idx = None
    for i in range(start + 1, len(lines)):
        if "conn.close()" in lines[i]:
            close_idx = i
            break
    if close_idx is None:
        raise ValueError("conn.close() inside init_db() not found while patching")
    close_line = lines[close_idx]
    indent = close_line[: len(close_line) - len(close_line.lstrip())]
    inserted = indent + PRAGMA_LINE
    lines.insert(close_idx, inserted)
    return "\n".join(lines), inserted


# --------------------------------------------------------------------------
# Restart + verification
# --------------------------------------------------------------------------
def wait_active(client, attempts=5, delay=1.5):
    status = None
    for _ in range(attempts):
        code, out, err = ssh_run(client, "systemctl is-active %s" % SERVICE)
        status = out.strip()
        if status == "active":
            return status
        time.sleep(delay)
    return status


def wait_health(host, attempts=5, delay=1.5):
    last = (None, None)
    for _ in range(attempts):
        try:
            code, data = health_check(host)
            last = (code, data)
            if code == 200 and data == {"ok": True}:
                return code, data
        except Exception as exc:  # noqa: BLE001
            last = (None, "error: %s" % exc)
        time.sleep(delay)
    return last


def restart_and_verify(client, host):
    code, out, err = ssh_run(client, "systemctl restart %s" % SERVICE)
    log("systemctl restart %s -> exit %s" % (SERVICE, code))
    time.sleep(2)

    status = wait_active(client)
    active_ok = status == "active"
    log("Post-restart systemctl is-active %s -> %r" % (SERVICE, status))

    hcode, hdata = wait_health(host)
    health_ok = hcode == 200 and hdata == {"ok": True}
    log("Post-restart GET /health -> HTTP %s %s" % (hcode, hdata))

    acode = None
    atext = ""
    auth_ok = False
    try:
        acode, atext = auth_check(host)
        auth_ok = acode == 401 and "unauthorized" in atext.lower()
    except Exception as exc:  # noqa: BLE001
        log("Post-restart auth check request failed: %s" % exc)
    log("Post-restart POST /check (bad key) -> HTTP %s %r" % (acode, atext[:200]))

    return active_ok, health_ok, auth_ok


def post_verify_patch_only(client):
    """Re-read the remote file and confirm both edits + backup. No restart.

    Returns a tuple of verification details for the structured report.
    """
    raw = sftp_read_bytes(client, REMOTE_PATH)
    content = raw.decode("utf-8")
    md5_after = hashlib.md5(raw).hexdigest()
    timeout_count = content.count("timeout=30")
    pragma_count = content.count("PRAGMA journal_mode")
    lines = content.split("\n")
    start = find_init_db_start(lines)
    wal_ok = False
    if start is not None:
        end = len(lines)
        for i in range(start + 1, len(lines)):
            if re.match(r"def \w+\s*\(", lines[i]):
                end = i
                break
        block = lines[start:end]
        for i, ln in enumerate(block):
            if PRAGMA_LINE in ln:
                # must be immediately before a conn.close() line
                for j in range(i + 1, len(block)):
                    if block[j].strip():
                        wal_ok = "conn.close()" in block[j]
                        break
                break
    code, out, err = ssh_run(
        client, "test -f %s && echo present || echo absent" % REMOTE_BAK
    )
    bak_present = out.strip() == "present"
    log("Post-verify: timeout=30 occurrences = %d (expected 3)" % timeout_count)
    log("Post-verify: PRAGMA journal_mode lines = %d (expected 1)" % pragma_count)
    log("Post-verify: WAL pragma immediately before conn.close() in init_db() = %s" % wal_ok)
    log("Post-verify: re-read md5 = %s" % md5_after)
    log("Post-verify: backup %s = %s" % (REMOTE_BAK, "present" if bak_present else "ABSENT"))
    ok = timeout_count == 3 and pragma_count == 1 and wal_ok and bak_present
    return ok, md5_after, timeout_count, pragma_count, wal_ok, bak_present


# --------------------------------------------------------------------------
# Budget fence (--budget-patch): constructs + verifies a NEW candidate, then
# uploads it patch-only. Never restarts/kills/signals anything.
# --------------------------------------------------------------------------
BUDGET_BEGIN_MARKER = "# ---------- budget fence v1 (std-lib only, crosses restarts) ----------"
BUDGET_END_MARKER = "# ---------- end budget fence ----------"

# The fence block, inserted right after the API_KEY/SE_USER/SE_SECRET block.
BUDGET_FENCE = '''# ---------- budget fence v1 (std-lib only, crosses restarts) ----------
import os as _os
import datetime as _datetime

def _env_int(name, default):
    try:
        return int(_os.environ.get(name, default))
    except (TypeError, ValueError):
        return default

DAILY_VERDICT_LIMIT = _env_int("AI_DAILY_VERDICT_LIMIT", 12)
RESOURCE_DAILY_LIMIT = _env_int("AI_RESOURCE_DAILY_LIMIT", 2)
HASH_DAILY_LIMIT = _env_int("AI_HASH_DAILY_LIMIT", 2)

def utc_today():
    return _datetime.datetime.now(_datetime.timezone.utc).strftime("%Y-%m-%d")

def _budget_connect():
    return sqlite3.connect(DB_PATH, timeout=30)

def budget_gate(resource):
    """Read-only fence run on every cache miss. Returns None when allowed,
    else a reason string for the 429 body."""
    day = utc_today()
    conn = _budget_connect()
    try:
        used = conn.execute(
            "SELECT verdicts FROM usage_day WHERE day = ?", (day,)
        ).fetchone()
        if used and used[0] >= DAILY_VERDICT_LIMIT:
            return "daily_verdict_budget"
        if resource:
            row = conn.execute(
                "SELECT budget_used FROM attempts_resource WHERE res = ? AND day = ?",
                (resource, day),
            ).fetchone()
            if row and row[0] >= RESOURCE_DAILY_LIMIT:
                return "per_image_retry_budget"
        return None
    finally:
        conn.close()

def budget_consume(resource, h):
    """Exact check-and-consume of the daily budget atomically. Returns None
    when the verdict attempt is granted, else a reason string."""
    day = utc_today()
    conn = _budget_connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        used = conn.execute(
            "SELECT verdicts FROM usage_day WHERE day = ?", (day,)
        ).fetchone()
        if used and used[0] >= DAILY_VERDICT_LIMIT:
            conn.rollback()
            return "daily_verdict_budget"
        conn.execute(
            "INSERT INTO usage_day(day, verdicts) VALUES (?, 1) "
            "ON CONFLICT(day) DO UPDATE SET verdicts = verdicts + 1",
            (day,),
        )
        if resource:
            conn.execute(
                "INSERT INTO attempts_resource(res, day, budget_used) VALUES (?, ?, 1) "
                "ON CONFLICT(res, day) DO UPDATE SET budget_used = budget_used + 1",
                (resource, day),
            )
        conn.execute(
            "INSERT INTO attempts_hash(hash, day, budget_used) VALUES (?, ?, 1) "
            "ON CONFLICT(hash, day) DO UPDATE SET budget_used = budget_used + 1",
            (h, day),
        )
        conn.commit()
        return None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
# ---------- end budget fence ----------'''

# Inserted in init_db() immediately before conn.close() (after the WAL pragma).
INIT_DB_TABLES = '''    conn.execute(
        "CREATE TABLE IF NOT EXISTS usage_day ("
        "day TEXT PRIMARY KEY, verdicts INTEGER)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS attempts_resource ("
        "res TEXT, day TEXT, budget_used INTEGER, PRIMARY KEY (res, day))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS attempts_hash ("
        "hash TEXT, day TEXT, budget_used INTEGER, PRIMARY KEY (hash, day))"
    )'''

# Inserted in check() immediately before ``row = cache_get(h)``.
CHECK_GATE = (
    '    resource = request.form.get("src") or ""\n'
    '    if not resource and request.form.get("url"):\n'
    '        resource = request.form["url"]\n'
    '    resource = resource.strip()[:2000]\n'
    '    reason = budget_gate(resource)\n'
    '    if reason:\n'
    '        # never break the client pipeline; the cached snapshot stays available\n'
    '        return jsonify(error="rate_limited", reason=reason), 429\n'
    '\n'
)

# Inserted in check() immediately before ``se = sightengine_check(data)``.
CHECK_CONSUME = (
    '        reason = budget_consume(resource, h)\n'
    '        if reason:\n'
    '            return jsonify(error="rate_limited", reason=reason), 429\n'
)


def _replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise ValueError(
            "anchor %r expected exactly 1 occurrence, found %d" % (label, count)
        )
    return text.replace(old, new)


def _function_block(content, func_name):
    """Return (lines, start, end) for the named top-level function body."""
    lines = content.split("\n")
    start = None
    for i, ln in enumerate(lines):
        if re.match(r"def %s\s*\(" % func_name, ln):
            start = i
            break
    if start is None:
        return lines, None, None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"def \w+\s*\(", lines[i]):
            end = i
            break
    return lines, start, end


def validate_live_anchors(content):
    """Check the four already-applied patches + the three insertion anchors."""
    d = {}
    d["timeout_count"] = content.count(CONNECT_NEW)
    _, istart, iend = _function_block(content, "init_db")
    pragma_ok = False
    if istart is not None:
        block = content.split("\n")[istart:iend]
        pi = None
        for i, ln in enumerate(block):
            if PRAGMA_LINE in ln:
                pi = i
                break
        if pi is not None:
            for j in range(pi + 1, len(block)):
                if "conn.close()" in block[j]:
                    pragma_ok = True
                    break
    d["pragma_before_close"] = pragma_ok
    d["image_safety"] = IMAGE_SAFETY_MARK in content
    d["local_check_catchall"] = 'print("local_check failed:"' in content
    d["cache_policy"] = CACHE_POLICY_MARK in content
    d["cache_guard"] = 'if source != "inconclusive":' in content
    d["anchor_secret"] = (
        'SE_SECRET = CONFIG["se_secret"]\n\napp = Flask(__name__)' in content
    )
    d["anchor_cache_get"] = "\n    row = cache_get(h)\n" in content
    d["anchor_sightengine"] = "\n        se = sightengine_check(data)\n" in content
    d["ok"] = (
        d["timeout_count"] == 3
        and d["pragma_before_close"]
        and d["image_safety"]
        and d["local_check_catchall"]
        and d["cache_policy"]
        and d["cache_guard"]
        and d["anchor_secret"]
        and d["anchor_cache_get"]
        and d["anchor_sightengine"]
    )
    return d


def build_budget_candidate(content):
    """Return the candidate text with all four budget insertions applied."""
    out = _replace_once(
        content,
        'SE_SECRET = CONFIG["se_secret"]\n\napp = Flask(__name__)',
        'SE_SECRET = CONFIG["se_secret"]\n\n' + BUDGET_FENCE + '\n\napp = Flask(__name__)',
        "secret-block",
    )
    out = _replace_once(
        out,
        '    conn.execute("PRAGMA journal_mode=WAL")\n    conn.close()',
        '    conn.execute("PRAGMA journal_mode=WAL")\n' + INIT_DB_TABLES + '\n    conn.close()',
        "init-db-close",
    )
    out = _replace_once(
        out,
        '    row = cache_get(h)',
        CHECK_GATE + '    row = cache_get(h)',
        "cache-get",
    )
    out = _replace_once(
        out,
        '        se = sightengine_check(data)',
        CHECK_CONSUME + '        se = sightengine_check(data)',
        "sightengine",
    )
    return out


def verify_budget_candidate(live_content, candidate):
    """Confirm the original markers survive and the new fence is whole."""
    d = {}
    d["live_timeout_count"] = live_content.count(CONNECT_NEW)
    d["candidate_timeout_count"] = candidate.count(CONNECT_NEW)
    per_fn = {}
    for fn in ("init_db", "cache_get", "cache_put"):
        _, start, end = _function_block(candidate, fn)
        ok = False
        if start is not None:
            ok = CONNECT_NEW in "\n".join(candidate.split("\n")[start:end])
        per_fn[fn] = ok
    d["fn_timeout"] = per_fn
    _, istart, iend = _function_block(candidate, "init_db")
    pragma_ok = False
    if istart is not None:
        block = candidate.split("\n")[istart:iend]
        pi = None
        for i, ln in enumerate(block):
            if PRAGMA_LINE in ln:
                pi = i
                break
        if pi is not None:
            for j in range(pi + 1, len(block)):
                if "conn.close()" in block[j]:
                    pragma_ok = True
                    break
    d["pragma_before_close"] = pragma_ok
    d["image_safety_count"] = candidate.count(IMAGE_SAFETY_MARK)
    d["cache_policy_count"] = candidate.count(CACHE_POLICY_MARK)
    d["fence_begin_count"] = candidate.count(BUDGET_BEGIN_MARKER)
    d["fence_end_count"] = candidate.count(BUDGET_END_MARKER)
    d["usage_day_count"] = candidate.count('CREATE TABLE IF NOT EXISTS usage_day')
    d["attempts_resource_count"] = candidate.count('CREATE TABLE IF NOT EXISTS attempts_resource')
    d["attempts_hash_count"] = candidate.count('CREATE TABLE IF NOT EXISTS attempts_hash')
    d["gate_count"] = candidate.count("reason = budget_gate(resource)")
    d["consume_count"] = candidate.count("reason = budget_consume(resource, h)")
    d["ok"] = (
        d["live_timeout_count"] == 3
        and all(per_fn.values())
        and d["pragma_before_close"]
        and d["image_safety_count"] == 1
        and d["cache_policy_count"] == 1
        and d["fence_begin_count"] == 1
        and d["fence_end_count"] == 1
        and d["usage_day_count"] == 1
        and d["attempts_resource_count"] == 1
        and d["attempts_hash_count"] == 1
        and d["gate_count"] == 1
        and d["consume_count"] == 1
    )
    return d


def extract_fence(candidate):
    """Return only the text between the fence begin/end markers."""
    out = []
    inside = False
    for ln in candidate.split("\n"):
        if ln.strip() == BUDGET_BEGIN_MARKER:
            inside = True
        if inside:
            out.append(ln)
        if inside and ln.strip() == BUDGET_END_MARKER:
            break
    return "\n".join(out)


def local_compile(text):
    fd, path = tempfile.mkstemp(suffix=".py", prefix="budget_candidate_")
    os.close(fd)
    try:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        proc = subprocess.run(
            [sys.executable, "-m", "py_compile", path],
            capture_output=True,
            text=True,
        )
        detail = (proc.stderr or proc.stdout).strip()
        return proc.returncode, detail
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def budget_logic_test(candidate):
    """Stdlib-only logic test of the extracted fence. Returns (ok, results)."""
    fence = extract_fence(candidate)
    results = []

    def check(label, cond):
        results.append((label, bool(cond)))
        log("  [%s] %s" % ("PASS" if cond else "FAIL", label))
        return bool(cond)

    workdir = tempfile.mkdtemp(prefix="budget_fence_")
    db_path = os.path.join(workdir, "test.db")

    ns = {"sqlite3": sqlite3, "DB_PATH": db_path}
    exec(compile(fence, "<budget-fence>", "exec"), ns)

    gate = ns["budget_gate"]
    consume = ns["budget_consume"]
    limit = ns["DAILY_VERDICT_LIMIT"]
    rlimit = ns["RESOURCE_DAILY_LIMIT"]

    # Mimic init_db(): create the three tables the fence expects.
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS usage_day ("
            "day TEXT PRIMARY KEY, verdicts INTEGER)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS attempts_resource ("
            "res TEXT, day TEXT, budget_used INTEGER, PRIMARY KEY (res, day))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS attempts_hash ("
            "hash TEXT, day TEXT, budget_used INTEGER, PRIMARY KEY (hash, day))"
        )
        conn.commit()
    finally:
        conn.close()

    def q(sql, args=()):
        c = sqlite3.connect(db_path)
        try:
            return c.execute(sql, args).fetchall()
        finally:
            c.close()

    def scalar(sql, args=(), default=None):
        rows = q(sql, args)
        return rows[0][0] if rows else default

    day = ns["utc_today"]()

    check("a. budget_gate(None) is None on empty db", gate(None) is None)

    r = consume("r1", "h1")
    check("b1. budget_consume('r1','h1') is None", r is None)
    check("b2. usage_day.verdicts == 1", scalar(
        "SELECT verdicts FROM usage_day WHERE day = ?", (day,)) == 1)
    check("b3. attempts_resource[('r1',day)] == 1", scalar(
        "SELECT budget_used FROM attempts_resource WHERE res = ? AND day = ?",
        ("r1", day)) == 1)
    check("b4. attempts_hash[('h1',day)] == 1", scalar(
        "SELECT budget_used FROM attempts_hash WHERE hash = ? AND day = ?",
        ("h1", day)) == 1)

    r = consume("r1", "h1")
    check("c1. repeat consume('r1','h1') is None", r is None)
    check("c2. usage_day.verdicts == 2", scalar(
        "SELECT verdicts FROM usage_day WHERE day = ?", (day,)) == 2)
    check("c3. attempts_resource[('r1',day)] == 2", scalar(
        "SELECT budget_used FROM attempts_resource WHERE res = ? AND day = ?",
        ("r1", day)) == 2)

    c = sqlite3.connect(db_path)
    try:
        c.execute("UPDATE usage_day SET verdicts = ? WHERE day = ?", (limit, day))
        c.commit()
    finally:
        c.close()
    before_res = scalar(
        "SELECT budget_used FROM attempts_resource WHERE res = ? AND day = ?",
        ("r1", day))
    before_hash = scalar(
        "SELECT budget_used FROM attempts_hash WHERE hash = ? AND day = ?",
        ("h1", day))
    r = consume("r1", "h1")
    check("d1. consume at daily limit returns 'daily_verdict_budget'",
          r == "daily_verdict_budget")
    check("d2. verdicts not incremented further (still == limit)", scalar(
        "SELECT verdicts FROM usage_day WHERE day = ?", (day,)) == limit)
    after_res = scalar(
        "SELECT budget_used FROM attempts_resource WHERE res = ? AND day = ?",
        ("r1", day))
    after_hash = scalar(
        "SELECT budget_used FROM attempts_hash WHERE hash = ? AND day = ?",
        ("h1", day))
    check("d3. rejection did not increment resource/hash counters",
          after_res == before_res and after_hash == before_hash)

    c = sqlite3.connect(db_path)
    try:
        c.execute("UPDATE usage_day SET verdicts = 0 WHERE day = ?", (day,))
        c.execute(
            "UPDATE attempts_resource SET budget_used = ? WHERE res = ? AND day = ?",
            (rlimit, "r1", day))
        c.commit()
    finally:
        c.close()
    r = gate("r1")
    check("e1. budget_gate(resource) == 'per_image_retry_budget' at resource cap",
          r == "per_image_retry_budget")
    r = consume("r1", "h-fresh")
    check("e2. budget_consume grants under the daily cap only (ignores "
          "per-resource cap)", r is None)

    ns["utc_today"] = lambda: "2000-01-02"
    check("f1. budget_gate(None) is None on a fresh day", gate(None) is None)
    r = consume(None, "h2")
    check("f2. fresh consume on a new day is None", r is None)
    check("f3. new day row written with verdicts == 1", scalar(
        "SELECT verdicts FROM usage_day WHERE day = ?", ("2000-01-02",)) == 1)

    ok = all(v for _, v in results)
    return ok, results


def _safe_health(host):
    try:
        return health_check(host)
    except Exception as exc:  # noqa: BLE001
        return None, "request failed: %s" % exc


def run_budget_patch():
    """--budget-patch: build + verify a candidate, then upload it patch-only."""
    if not os.path.exists(ENV_PATH):
        log("ABORT: .env not found at %s" % ENV_PATH)
        return EXIT_ENV

    env = load_env(ENV_PATH)
    host = get_secret(env, "VULTR_SSH_HOST")
    user = get_secret(env, "VULTR_SSH_USER")
    password = get_secret(env, "VULTR_SSH_PASSWORD")
    missing = [
        name
        for name, val in (
            ("VULTR_SSH_HOST", host),
            ("VULTR_SSH_USER", user),
            ("VULTR_SSH_PASSWORD", password),
        )
        if not val
    ]
    if missing:
        log("ABORT: missing or placeholder value(s) in .env for: %s" % ", ".join(missing))
        return EXIT_ENV

    client = None
    try:
        log("Budget-patch mode (PATCH-ONLY; no restart/kill/signal, ever).")
        log("Connecting over SSH to the configured host ...")
        client = ssh_connect(host, user, password)
        log("SSH connected.")

        # ---- FETCH LIVE FILE ------------------------------------------
        try:
            raw = sftp_read_bytes(client, REMOTE_PATH)
            content = raw.decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            log("ABORT: could not read %s: %s. No changes made." % (REMOTE_PATH, exc))
            return EXIT_VALIDATE
        md5_before = hashlib.md5(raw).hexdigest()
        log("Fetched %s (%d bytes). md5=%s" % (REMOTE_PATH, len(raw), md5_before))

        # ---- IDEMPOTENT: FENCE ALREADY PRESENT ------------------------
        if BUDGET_BEGIN_MARKER in content:
            log("Remote file already contains the budget fence. No upload performed.")
            hcode, hdata = _safe_health(host)
            log("GET /health -> HTTP %s %s" % (hcode, hdata))
            log("")
            log("PATCH-ONLY COMPLETE (already patched). New fence activates on the "
                "next manual restart (Ctrl+C then: "
                "cd /root/ai-check && venv/bin/python app.py).")
            return EXIT_OK

        # ---- VALIDATE LIVE ANCHORS ------------------------------------
        anchors = validate_live_anchors(content)
        log("Anchor check: timeout=30 count=%d (expected 3), pragma_before_close=%s, "
            "image_safety=%s, local_check_catchall=%s, cache_policy=%s, cache_guard=%s"
            % (anchors["timeout_count"], anchors["pragma_before_close"],
               anchors["image_safety"], anchors["local_check_catchall"],
               anchors["cache_policy"], anchors["cache_guard"]))
        log("Insertion anchors: secret_block=%s, cache_get=%s, sightengine=%s"
            % (anchors["anchor_secret"], anchors["anchor_cache_get"],
               anchors["anchor_sightengine"]))
        if not anchors["ok"]:
            log("ABORT: one or more required anchors are missing from the fetched "
                "file. No changes made.")
            return EXIT_PRECHECK
        log("All four existing patches + all insertion anchors are present.")

        # ---- BUILD CANDIDATE IN MEMORY --------------------------------
        try:
            candidate = build_budget_candidate(content)
        except ValueError as exc:
            log("ABORT: %s. No changes made." % exc)
            return EXIT_VALIDATE

        v = verify_budget_candidate(content, candidate)
        log("Candidate check: live timeout=30=%d, candidate timeout=30=%d "
            "(3 original + 1 fence helper), per-function timeout=%s"
            % (v["live_timeout_count"], v["candidate_timeout_count"], v["fn_timeout"]))
        log("Candidate check: pragma_before_close=%s, image_safety_mark=%d, "
            "cache_policy_mark=%d, fence_markers=%d/%d, tables=%d/%d/%d "
            "(usage_day/attempts_resource/attempts_hash)"
            % (v["pragma_before_close"], v["image_safety_count"],
               v["cache_policy_count"], v["fence_begin_count"], v["fence_end_count"],
               v["usage_day_count"], v["attempts_resource_count"],
               v["attempts_hash_count"]))
        log("Candidate check: gate_inserts=%d, consume_inserts=%d"
            % (v["gate_count"], v["consume_count"]))
        if not v["ok"]:
            log("ABORT: candidate marker verification failed. No changes made.")
            return EXIT_VALIDATE
        log("Candidate marker verification passed.")

        # ---- LOCAL PY_COMPILE -----------------------------------------
        code, detail = local_compile(candidate)
        log("Local py_compile (project venv) -> exit %d%s"
            % (code, (": " + detail) if detail else ""))
        if code != 0:
            log("ABORT: local py_compile failed. No changes made.")
            return EXIT_VALIDATE

        # ---- STANDALONE LOGIC TEST ------------------------------------
        log("Standalone fence logic test:")
        test_ok, results = budget_logic_test(candidate)
        log("Logic test: %d/%d assertions passed." % (
            sum(1 for _, ok in results if ok), len(results)))
        if not test_ok:
            log("ABORT: logic test failed. No changes made.")
            return EXIT_VALIDATE

        candidate_md5 = hashlib.md5(candidate.encode("utf-8")).hexdigest()
        log("Candidate md5=%s (%d bytes)."
            % (candidate_md5, len(candidate.encode("utf-8"))))

        # ---- BACKUP (new name only) -----------------------------------
        code, out, err = ssh_run(
            client, "cp -f %s %s" % (REMOTE_PATH, REMOTE_BAK_BUDGET))
        log("Backup: cp -f %s %s -> exit %s"
            % (REMOTE_PATH, REMOTE_BAK_BUDGET, code))
        if code != 0:
            log("ABORT: backup copy failed (%s). No changes made."
                % (err.strip() or code))
            return EXIT_FAILED
        code, out, err = ssh_run(
            client, "test -f %s && echo present || echo absent" % REMOTE_BAK_BUDGET)
        log("Backup file %s: %s" % (REMOTE_BAK_BUDGET, out.strip()))
        if out.strip() != "present":
            log("ABORT: backup file not present. No changes made.")
            return EXIT_FAILED

        # ---- UPLOAD ----------------------------------------------------
        try:
            sftp_write(client, REMOTE_PATH, candidate)
        except Exception as exc:  # noqa: BLE001
            log("ABORT: upload failed: %s. Attempting file-only rollback ..." % exc)
            ssh_run(client, "cp -f %s %s" % (REMOTE_BAK_BUDGET, REMOTE_PATH))
            log("Rollback (file only, no restart) attempted. FAILED.")
            return EXIT_FAILED
        log("Uploaded candidate to %s (mode 0644)." % REMOTE_PATH)

        # ---- VERIFY MD5 ------------------------------------------------
        raw_after = sftp_read_bytes(client, REMOTE_PATH)
        md5_after = hashlib.md5(raw_after).hexdigest()
        md5_match = md5_after == candidate_md5
        log("Re-read md5=%s (match=%s)" % (md5_after, md5_match))
        if not md5_match:
            log("ABORT: remote bytes do not match candidate. Investigate manually.")
            return EXIT_FAILED

        # ---- REMOTE COMPILE -------------------------------------------
        code, out, err = ssh_run(
            client,
            "cd /root/ai-check && venv/bin/python -m py_compile %s" % REMOTE_PATH)
        detail = (err.strip() or out.strip())
        log("Remote py_compile -> exit %s%s" % (code, (": " + detail) if detail else ""))
        if code != 0:
            log("ABORT: remote py_compile failed. Uploaded file left in place; "
                "backup at %s." % REMOTE_BAK_BUDGET)
            return EXIT_FAILED

        # ---- HEALTH ----------------------------------------------------
        hcode, hdata = _safe_health(host)
        log("GET /health -> HTTP %s %s" % (hcode, hdata))
        if not (hcode == 200 and hdata == {"ok": True}):
            log("WARNING: /health did not return {'ok': true}; the running OLD "
                "process may be stopped. Upload + verification still succeeded.")

        log("")
        log("=== SUMMARY (OK, budget-patch, PATCH-ONLY) ===")
        log("md5 before: %s" % md5_before)
        log("md5 after : %s" % md5_after)
        log("Backup: %s" % REMOTE_BAK_BUDGET)
        log("Local py_compile: exit 0; remote py_compile: exit 0.")
        log("No process was started, restarted, killed or signaled; no live SQL; "
            "no /check POST.")
        log("")
        log("PATCH-ONLY COMPLETE. New fence activates on the next manual restart "
            "(Ctrl+C then: cd /root/ai-check && venv/bin/python app.py).")
        return EXIT_OK

    except Exception as exc:  # noqa: BLE001
        log("FAILED: unexpected error: %s" % exc)
        return EXIT_FAILED
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    if "--budget-patch" in sys.argv:
        # Always patch-only: never restarts/kills/signals, regardless of flags.
        return run_budget_patch()

    if not os.path.exists(ENV_PATH):
        log("ABORT: .env not found at %s" % ENV_PATH)
        return EXIT_ENV

    env = load_env(ENV_PATH)
    host = get_secret(env, "VULTR_SSH_HOST")
    user = get_secret(env, "VULTR_SSH_USER")
    password = get_secret(env, "VULTR_SSH_PASSWORD")

    missing = [
        name
        for name, val in (
            ("VULTR_SSH_HOST", host),
            ("VULTR_SSH_USER", user),
            ("VULTR_SSH_PASSWORD", password),
        )
        if not val
    ]
    if missing:
        log("ABORT: missing or placeholder value(s) in .env for: %s" % ", ".join(missing))
        log("Fill in .env (replace the text inside the quotes) and re-run. No changes made.")
        return EXIT_ENV

    client = None
    proc_state = "unknown"
    try:
        log("Connecting over SSH to %s ..." % host)
        client = ssh_connect(host, user, password)
        log("SSH connected.")

        # ---- PRE-CHECK: PROCESS STATE (OBSERVE ONLY) -------------------
        if NO_RESTART:
            log("Pre-check 1 (observe only, patch-only mode):")
            _, ss_out, _ = ssh_run(client, "ss -ltnp | grep ':80 ' || echo NO_PORT80_LISTENER")
            for line in ss_out.strip().splitlines():
                log("    ss: %s" % line)
            _, ps_out, _ = ssh_run(client, "ps aux | grep '[a]pp.py' | head -3")
            ps_lines = [ln for ln in ps_out.strip().splitlines() if ln.strip()]
            if ps_lines:
                for line in ps_lines:
                    log("    ps: %s" % line)
                proc_state = "running"
            else:
                log("    ps: (no app.py process found)")
                proc_state = "not running"
            log("Process state observed: app.py %s (no action taken)." % proc_state)
        else:
            code, out, err = ssh_run(client, "systemctl is-active %s" % SERVICE)
            status = out.strip()
            log("Pre-check 1: systemctl is-active %s -> %r (exit %s)" % (SERVICE, status, code))
            if status != "active":
                log("ABORT: service is not active. No changes made.")
                return EXIT_PRECHECK

        # ---- HEALTH PROBE (never fatal in patch-only mode) ------------
        hcode = None
        hdata = None
        health_ok = False
        try:
            hcode, hdata = health_check(host)
            health_ok = hcode == 200 and hdata == {"ok": True}
            log("Pre-check 2: GET /health -> HTTP %s %s" % (hcode, hdata))
        except Exception as exc:  # noqa: BLE001
            log("Pre-check 2: GET /health verbose failure: %s" % exc)
        if NO_RESTART:
            if health_ok:
                log("Health: app responded normally; continuing (patch-only mode).")
            else:
                log("Health: app currently stopped (expected); continuing (patch-only mode).")
        else:
            if not health_ok:
                log("ABORT: health check did not return {'ok': true}. No changes made.")
                return EXIT_PRECHECK

        # ---- FETCH REMOTE FILE ----------------------------------------
        try:
            raw = sftp_read_bytes(client, REMOTE_PATH)
            content = raw.decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            log("ABORT: could not read %s: %s. No changes made." % (REMOTE_PATH, exc))
            return EXIT_VALIDATE
        md5_before = hashlib.md5(raw).hexdigest()
        log("Fetched %s (%d bytes). md5=%s" % (REMOTE_PATH, len(raw), md5_before))

        # ---- IDEMPOTENT: ALREADY PATCHED ------------------------------
        if "timeout=30" in content:
            log("Remote file already appears patched (timeout=30 present). Idempotent mode.")
            code, out, err = ssh_run(
                client, "test -f %s && echo present || echo absent" % REMOTE_BAK
            )
            bak_present = out.strip() == "present"
            log("Backup %s: %s" % (REMOTE_BAK, "present" if bak_present else "ABSENT"))
            if NO_RESTART:
                log("")
                log("=== SUMMARY (ALREADY_PATCHED, patch-only) ===")
                log("Process state: %s (no action taken)." % proc_state)
                log("Health: %s" % ("ok" if health_ok else "app currently stopped (expected)"))
                log("Patch: not applied (already present).")
                log("md5 before: %s" % md5_before)
                log("md5 after : %s (unchanged)" % md5_before)
                log("Backup: %s" % ("present" if bak_present else "ABSENT"))
                log("No process was started, restarted, killed or signaled; "
                    "no server file other than app.py (+ its backup) was touched.")
                return EXIT_OK
            active_ok, health_ok, auth_ok = restart_and_verify(client, host)
            log("")
            log("=== SUMMARY (ALREADY_PATCHED) ===")
            log("Pre-check: service active, /health ok.")
            log("Patch: not applied (already present).")
            log("Restart: is-active=%s, health=%s, 401-auth=%s" % (active_ok, health_ok, auth_ok))
            log("No other server changes were made.")
            return EXIT_OK

        # ---- VALIDATE --------------------------------------------------
        count = content.count(CONNECT_OLD)
        block = init_db_block(content)
        log("Validation: occurrences of %r = %d" % (CONNECT_OLD, count))
        if count != 3:
            log("ABORT: expected exactly 3 connect lines, found %d. No changes made." % count)
            return EXIT_VALIDATE
        if block is None:
            log("ABORT: init_db() not found. No changes made.")
            return EXIT_VALIDATE
        if "conn.close()" not in block:
            log("ABORT: init_db() does not contain conn.close(). No changes made.")
            return EXIT_VALIDATE
        if "journal_mode" in block:
            log("ABORT: init_db() already contains journal_mode. No changes made.")
            return EXIT_VALIDATE
        log("Validation passed: 3 connect lines, init_db() has conn.close() and no journal_mode.")

        # ---- APPLY PATCH IN MEMORY ------------------------------------
        try:
            patched, inserted = apply_patch(content)
        except ValueError as exc:
            log("ABORT: %s. No changes made." % exc)
            return EXIT_VALIDATE

        log("")
        log("--- changed lines (before -> after) ---")
        log("- %s" % CONNECT_OLD)
        log("+ %s" % CONNECT_NEW)
        log("  (applied to all 3 occurrences: init_db(), cache_get(), cache_put())")
        log("  init_db(): inserted immediately before conn.close():")
        log("+ %s" % inserted)
        log("---------------------------------------")
        log("")

        # ---- BACKUP ----------------------------------------------------
        code, out, err = ssh_run(client, "cp -n %s %s" % (REMOTE_PATH, REMOTE_BAK))
        log("Backup: cp -n %s %s -> exit %s" % (REMOTE_PATH, REMOTE_BAK, code))
        code, out, err = ssh_run(
            client, "test -f %s && echo present || echo absent" % REMOTE_BAK
        )
        log("Backup file %s: %s" % (REMOTE_BAK, out.strip()))

        # ---- UPLOAD ----------------------------------------------------
        try:
            sftp_write(client, REMOTE_PATH, patched)
        except Exception as exc:  # noqa: BLE001
            log("ABORT: upload failed: %s. Attempting rollback from backup ..." % exc)
            if NO_RESTART:
                ssh_run(client, "cp %s %s" % (REMOTE_BAK, REMOTE_PATH))
                log("Rollback (file only, no restart) attempted. FAILED.")
            else:
                ssh_run(client, "cp %s %s && systemctl restart %s" % (REMOTE_BAK, REMOTE_PATH, SERVICE))
                log("Rollback attempted. FAILED.")
            return EXIT_FAILED
        log("Uploaded patched file to %s (mode 0644)." % REMOTE_PATH)

        # ---- PATCH-ONLY: POST-VERIFY, NO RESTART ----------------------
        if NO_RESTART:
            ok, md5_after, tc, pc, wal_ok, bak_ok = post_verify_patch_only(client)
            log("")
            if ok:
                log("=== SUMMARY (OK, patch-only) ===")
                log("Process state: %s (no action taken)." % proc_state)
                log("Health: %s" % ("ok" if health_ok else "app currently stopped (expected)"))
                log("Patch applied: 3x connect timeout=30, WAL pragma in init_db().")
                log("md5 before: %s" % md5_before)
                log("md5 after : %s" % md5_after)
                log("Backup: present (%s)" % REMOTE_BAK)
                log("No process was started, restarted, killed or signaled; "
                    "no server file other than app.py (+ its backup) was touched.")
                return EXIT_OK
            log("=== SUMMARY (FAILED, patch-only) ===")
            log("Process state: %s (no action taken)." % proc_state)
            log("Post-verify did not confirm all edits + backup "
                "(timeout=30=%d, pragma=%d, wal_before_close=%s, backup=%s)."
                % (tc, pc, wal_ok, bak_ok))
            log("md5 before: %s" % md5_before)
            log("md5 after : %s" % md5_after)
            log("No process was started, restarted, killed or signaled.")
            return EXIT_FAILED

        # ---- RESTART + VERIFY -----------------------------------------
        active_ok, health_ok, auth_ok = restart_and_verify(client, host)

        if not (active_ok and health_ok and auth_ok):
            log("Verification FAILED after restart. Rolling back immediately ...")
            code, out, err = ssh_run(
                client,
                "cp %s %s && systemctl restart %s" % (REMOTE_BAK, REMOTE_PATH, SERVICE),
            )
            log("Rollback command exit %s" % code)
            time.sleep(2)
            try:
                hcode, hdata = health_check(host)
                log("Post-rollback GET /health -> HTTP %s %s" % (hcode, hdata))
            except Exception as exc:  # noqa: BLE001
                log("Post-rollback health request failed: %s" % exc)
            log("")
            log("=== SUMMARY (FAILED) ===")
            log("Patch was applied then rolled back from %s." % REMOTE_BAK)
            log("Restart: is-active=%s, health=%s, 401-auth=%s" % (active_ok, health_ok, auth_ok))
            return EXIT_FAILED

        # ---- SUCCESS ---------------------------------------------------
        log("")
        log("=== SUMMARY (OK) ===")
        log("Pre-check: service active, /health ok.")
        log("Patch applied: 3x connect timeout=30, WAL pragma in init_db().")
        log("Backup path on server: %s" % REMOTE_BAK)
        log("Restart: is-active=%s, health=%s, 401-auth=%s" % (active_ok, health_ok, auth_ok))
        log("No other server changes were made.")
        return EXIT_OK

    except Exception as exc:  # noqa: BLE001
        log("FAILED: unexpected error: %s" % exc)
        return EXIT_FAILED
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    sys.exit(main())
