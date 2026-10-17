#!/usr/bin/env python3
"""One-off ops tool (v3): surgical budget-fence fix for /root/ai-check/app.py.

Two edits in one pass:
  EDIT 1  move the budget gate from BEFORE ``row = cache_get(h)`` to AFTER the
          cached-return branch (so cache hits stay free during exhaustion).
  EDIT 2  replace ``budget_gate(resource)`` with ``budget_gate(resource, h)``
          inside the fence and enforce the hash cap at the gate.

Hard discipline:
  * Never prints file contents or any .env value.
  * No restarts / kills / signals.
  * No SQL against the live cache.db.
  * No POSTs; HTTP limited to GET /health.
  * Only /root/ai-check/app.py is touched remotely (plus py_compile bytecode).

Run with the project venv:
    .venv\\Scripts\\python.exe tools\\vultr_patch_v3.py
"""

import hashlib
import os
import re
import sqlite3
import subprocess
import sys
import tempfile

import paramiko
import requests

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(REPO_ROOT, ".env")
RUNBOOK_PATH = os.path.join(REPO_ROOT, "vultr-server-info", "RUNBOOK.md")

REMOTE_PATH = "/root/ai-check/app.py"
EXPECTED_MD5_BEFORE = "d6bb362aa81d8a01390421b34f6b8ba5"

BUDGET_BEGIN_MARKER = "# ---------- budget fence v1 (std-lib only, crosses restarts) ----------"
BUDGET_END_MARKER = "# ---------- end budget fence ----------"
IMAGE_SAFETY_MARK = "# image-safety: chunk failures tolerated"
CACHE_POLICY_MARK = "# cache-policy: inconclusive results are never cached"

CACHED_RETURN_STMT = (
    "        return jsonify(id=h, score=row[0], source=row[1], cached=True)"
)
CACHE_GET_LINE = "    row = cache_get(h)"
GATE_FIRST_LINE = '    resource = request.form.get("src") or ""'
GATE_RETURN_429 = 'return jsonify(error="rate_limited", reason=reason), 429'
CONNECT_MARK = "conn = sqlite3.connect(DB_PATH, timeout=30)"

GATE_BLOCK = """    # budget gate runs after the cache lookup so already-verified images
    # keep working even while the provider budget is exhausted
    resource = request.form.get("src") or ""
    if not resource and request.form.get("url"):
        resource = request.form["url"]
    resource = resource.strip()[:2000]
    reason = budget_gate(resource, h)
    if reason:
        return jsonify(error="rate_limited", reason=reason), 429"""

NEW_DEF = '''def budget_gate(resource, h):
    """Read-only fence run after the cache lookup on every cache miss.
    Returns None when allowed, else a reason string for the 429 body."""
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
        row = conn.execute(
            "SELECT budget_used FROM attempts_hash WHERE hash = ? AND day = ?",
            (h, day),
        ).fetchone()
        if row and row[0] >= HASH_DAILY_LIMIT:
            return "per_image_retry_budget"
        return None
    finally:
        conn.close()'''

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ENV = 2
EXIT_PRECHECK = 3
EXIT_VALIDATE = 4

RESULTS = []


def log(msg):
    print(msg, flush=True)


def check(label, cond):
    ok = bool(cond)
    RESULTS.append((label, ok))
    log("  [%s] %s" % ("PASS" if ok else "FAIL", label))
    return ok


# --------------------------------------------------------------------------
# .env loading (tolerant) -- values are never printed
# --------------------------------------------------------------------------
def load_env(path):
    values = {}
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            values[key] = val
    return values


def get_secret(env, key):
    val = env.get(key, "")
    if not val or "PUT " in val:
        return None
    return val


# --------------------------------------------------------------------------
# SSH / SFTP
# --------------------------------------------------------------------------
def ssh_connect(host, user, password):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host, username=user, password=password,
        timeout=20, banner_timeout=20, auth_timeout=20,
        look_for_keys=False, allow_agent=False,
    )
    return client


def ssh_run(client, command):
    stdin, stdout, stderr = client.exec_command(command, timeout=30)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    return code, out, err


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
# Derived anchors
# --------------------------------------------------------------------------
def derive_old_gate(content):
    lines = content.split("\n")
    try:
        ci = lines.index(CACHE_GET_LINE)
    except ValueError:
        raise ValueError("cache_get line not found")
    bs = None
    for j in range(ci - 1, -1, -1):
        if lines[j].strip() == GATE_FIRST_LINE.strip():
            bs = j
            break
    if bs is None:
        raise ValueError("gate first line not found before cache_get")
    if not any(lines[j].strip() == GATE_RETURN_429 for j in range(bs, ci)):
        raise ValueError("gate 429 return not found before cache_get")
    # Exact text from the gate's first line up to (excluding) cache_get, including
    # the newline that terminates each line. Removing this leaves the original
    # ``hexdigest()`` newline + one blank line before cache_get.
    return "\n".join(lines[bs:ci]) + "\n"


def derive_old_def(content):
    lines = content.split("\n")
    g0 = None
    for i, ln in enumerate(lines):
        if ln.startswith("def budget_gate("):
            g0 = i
            break
    if g0 is None:
        raise ValueError("def budget_gate not found")
    c0 = None
    for i in range(g0 + 1, len(lines)):
        if lines[i].startswith("def budget_consume("):
            c0 = i
            break
    if c0 is None:
        raise ValueError("def budget_consume not found after budget_gate")
    return "\n".join(lines[g0:c0])


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise ValueError("anchor %r expected 1 occurrence, found %d" % (label, count))
    return text.replace(old, new)


# --------------------------------------------------------------------------
# Candidate construction
# --------------------------------------------------------------------------
def build_candidate(content):
    # EDIT 1a: remove the old-position gate.
    old_gate = derive_old_gate(content)
    if content.count(old_gate) != 1:
        raise ValueError("old gate block count %d != 1" % content.count(old_gate))
    out = content.replace(old_gate, "")

    # EDIT 1b: insert the improved gate after the cached-return branch.
    out = replace_once(out, CACHED_RETURN_STMT, CACHED_RETURN_STMT + "\n\n" + GATE_BLOCK,
                       "cached-return")

    # EDIT 2: replace the budget_gate def (resource) -> (resource, h).
    old_def = derive_old_def(out)
    if out.count(old_def) != 1:
        raise ValueError("old budget_gate def count %d != 1" % out.count(old_def))
    new_region = NEW_DEF + ("\n" if old_def.endswith("\n") else "")
    out = out.replace(old_def, new_region)
    return out


# --------------------------------------------------------------------------
# Marker audit
# --------------------------------------------------------------------------
def function_block(content, name):
    lines = content.split("\n")
    start = None
    for i, ln in enumerate(lines):
        if re.match(r"def %s\s*\(" % name, ln):
            start = i
            break
    if start is None:
        return None
    for i in range(start + 1, len(lines)):
        if re.match(r"def \w+\s*\(", lines[i]):
            return "\n".join(lines[start:i])
    return "\n".join(lines[start:])


def audit(content):
    log("Marker audit:")
    timeout_count = content.count(CONNECT_MARK)
    check("conn=sqlite3.connect(DB_PATH, timeout=30) count == 3 (%d)" % timeout_count,
          timeout_count == 3)
    pragma_count = content.count("PRAGMA journal_mode")
    check("PRAGMA journal_mode count == 1 (%d)" % pragma_count, pragma_count == 1)
    check("marker '# image-safety...' count == 1",
          content.count(IMAGE_SAFETY_MARK) == 1)
    check("marker '# cache-policy...' count == 1",
          content.count(CACHE_POLICY_MARK) == 1)
    check("fence begin marker count == 1",
          content.count(BUDGET_BEGIN_MARKER) == 1)
    check("fence end marker count == 1",
          content.count(BUDGET_END_MARKER) == 1)

    gate_count = content.count(GATE_BLOCK)
    check("new gate block occurs exactly once (%d)" % gate_count, gate_count == 1)
    gidx = content.find(GATE_BLOCK)
    ridx = content.find(CACHED_RETURN_STMT)
    check("gate index > cached-return index (%d > %d)" % (gidx, ridx),
          gidx != -1 and ridx != -1 and gidx > ridx)

    check_body = function_block(content, "check") or ""
    call_count = check_body.count("budget_gate(resource, h)")
    check("budget_gate(resource, h) in check() == 1 (%d)" % call_count,
          call_count == 1)

    # no leftover old-position / old-signature gate call anywhere
    check("no old 'budget_gate(resource)' call remains",
          "budget_gate(resource)" not in content)
    check("budget_gate def takes (resource, h)",
          "def budget_gate(resource, h):" in content)
    check("hash cap enforced at gate",
          'FROM attempts_hash WHERE hash = ? AND day = ?' in function_block(content, "budget_gate"))
    return all(ok for _, ok in RESULTS)


# --------------------------------------------------------------------------
# Local compile
# --------------------------------------------------------------------------
def local_compile(text):
    fd, path = tempfile.mkstemp(suffix=".py", prefix="v3_candidate_")
    os.close(fd)
    try:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        proc = subprocess.run(
            [sys.executable, "-m", "py_compile", path],
            capture_output=True, text=True,
        )
        return proc.returncode, (proc.stderr or proc.stdout).strip()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


# --------------------------------------------------------------------------
# Logic test
# --------------------------------------------------------------------------
def extract_fence(candidate):
    out, inside = [], False
    for ln in candidate.split("\n"):
        if ln.strip() == BUDGET_BEGIN_MARKER:
            inside = True
        if inside:
            out.append(ln)
        if inside and ln.strip() == BUDGET_END_MARKER:
            break
    return "\n".join(out)


def logic_test(candidate):
    fence = extract_fence(candidate)
    log("Logic test:")
    workdir = tempfile.mkdtemp(prefix="v3_fence_")
    db_path = os.path.join(workdir, "test.db")

    ns = {"sqlite3": sqlite3, "DB_PATH": db_path}
    exec(compile(fence, "<budget-fence>", "exec"), ns)

    gate = ns["budget_gate"]
    consume = ns["budget_consume"]
    dlimit = ns["DAILY_VERDICT_LIMIT"]
    rlimit = ns["RESOURCE_DAILY_LIMIT"]
    hlimit = ns["HASH_DAILY_LIMIT"]

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS usage_day ("
                     "day TEXT PRIMARY KEY, verdicts INTEGER)")
        conn.execute("CREATE TABLE IF NOT EXISTS attempts_resource ("
                     "res TEXT, day TEXT, budget_used INTEGER, PRIMARY KEY (res, day))")
        conn.execute("CREATE TABLE IF NOT EXISTS attempts_hash ("
                     "hash TEXT, day TEXT, budget_used INTEGER, PRIMARY KEY (hash, day))")
        conn.commit()
    finally:
        conn.close()

    def scalar(sql, args=(), default=None):
        c = sqlite3.connect(db_path)
        try:
            rows = c.execute(sql, args).fetchall()
            return rows[0][0] if rows else default
        finally:
            c.close()

    def exec1(sql, args=()):
        c = sqlite3.connect(db_path)
        try:
            c.execute(sql, args)
            c.commit()
        finally:
            c.close()

    day = ns["utc_today"]()

    check("a. budget_gate(None,'hX') is None on empty db", gate(None, "hX") is None)

    check("b1. consume('r1','h1') grants", consume("r1", "h1") is None)
    check("b2. verdicts == 1", scalar(
        "SELECT verdicts FROM usage_day WHERE day=?", (day,)) == 1)
    check("b3. attempts_resource == 1", scalar(
        "SELECT budget_used FROM attempts_resource WHERE res=? AND day=?",
        ("r1", day)) == 1)
    check("b4. attempts_hash == 1", scalar(
        "SELECT budget_used FROM attempts_hash WHERE hash=? AND day=?",
        ("h1", day)) == 1)
    check("b5. repeat consume grants again", consume("r1", "h1") is None)
    check("b6. verdicts == 2", scalar(
        "SELECT verdicts FROM usage_day WHERE day=?", (day,)) == 2)
    check("b7. attempts_resource == 2", scalar(
        "SELECT budget_used FROM attempts_resource WHERE res=? AND day=?",
        ("r1", day)) == 2)
    check("b8. attempts_hash == 2", scalar(
        "SELECT budget_used FROM attempts_hash WHERE hash=? AND day=?",
        ("h1", day)) == 2)
    check("b9. gate('r1','h1') == per_image_retry_budget (hash cap)",
          gate("r1", "h1") == "per_image_retry_budget")
    check("b10. gate('otherR','h1') == per_image_retry_budget",
          gate("otherR", "h1") == "per_image_retry_budget")
    check("b11. gate(None,'hOther') is None", gate(None, "hOther") is None)

    exec1("INSERT INTO attempts_resource(res, day, budget_used) VALUES (?, ?, ?)",
          ("r2", day, rlimit))
    check("c. gate('r2','hFresh') == per_image_retry_budget (resource cap)",
          gate("r2", "hFresh") == "per_image_retry_budget")

    exec1("UPDATE usage_day SET verdicts=? WHERE day=?", (dlimit, day))
    check("d. gate(any) == daily_verdict_budget at daily cap",
          gate("whatever", "whateverH") == "daily_verdict_budget")

    ns["utc_today"] = lambda: "2000-01-02"
    check("e1. gate(None,'hZ') is None on a fresh day", gate(None, "hZ") is None)
    check("e2. consume(None,'h2') grants", consume(None, "h2") is None)
    check("e3. new day row verdicts == 1", scalar(
        "SELECT verdicts FROM usage_day WHERE day=?", ("2000-01-02",)) == 1)

    exec1("UPDATE usage_day SET verdicts=? WHERE day=?",
          (dlimit, "2000-01-02"))
    before = (scalar("SELECT verdicts FROM usage_day WHERE day=?", ("2000-01-02",)),
              scalar("SELECT budget_used FROM attempts_resource WHERE res=? AND day=?",
                     ("rF", "2000-01-02")),
              scalar("SELECT budget_used FROM attempts_hash WHERE hash=? AND day=?",
                     ("hF", "2000-01-02")))
    r = consume("rF", "hF")
    after = (scalar("SELECT verdicts FROM usage_day WHERE day=?", ("2000-01-02",)),
             scalar("SELECT budget_used FROM attempts_resource WHERE res=? AND day=?",
                    ("rF", "2000-01-02")),
             scalar("SELECT budget_used FROM attempts_hash WHERE hash=? AND day=?",
                    ("hF", "2000-01-02")))
    check("f1. consume at daily cap returns daily_verdict_budget",
          r == "daily_verdict_budget")
    check("f2. rejection increments nothing", after == before)

    total = len(RESULTS)
    passed = sum(1 for _, ok in RESULTS if ok)
    log("Logic test totals: %d/%d PASS" % (passed, total))
    return all(ok for _, ok in RESULTS)


# --------------------------------------------------------------------------
# RUNBOOK parity
# --------------------------------------------------------------------------
def runbook_parity(candidate):
    with open(RUNBOOK_PATH, "r", encoding="utf-8", newline="") as fh:
        rb = fh.read()
    lines = rb.split("\n")
    si = None
    for i, ln in enumerate(lines):
        if ln.strip() == "cat > /root/ai-check/app.py << 'EOF'":
            si = i
            break
    if si is None:
        check("RUNBOOK Step 6 heredoc header found", False)
        return False
    ei = None
    for i in range(si + 1, len(lines)):
        if lines[i].strip() == "EOF":
            ei = i
            break
    if ei is None:
        check("RUNBOOK Step 6 heredoc terminator found", False)
        return False

    body = "\n".join(lines[si + 1:ei])
    cand_lines = candidate.split("\n")
    body_md5 = hashlib.md5(body.encode("utf-8")).hexdigest()
    cand_md5 = hashlib.md5(candidate.encode("utf-8")).hexdigest()
    check("RUNBOOK heredoc body == candidate md5 (%s)" % body_md5,
          body_md5 == cand_md5)

    if body_md5 != cand_md5:
        lines[si + 1:ei] = cand_lines
        rb2 = "\n".join(lines)
        with open(RUNBOOK_PATH, "w", encoding="utf-8", newline="") as fh:
            fh.write(rb2)
        log("STATUS: RUNBOOK Step 6 heredoc replaced with final candidate.")
        body = "\n".join(cand_lines)
        check("RUNBOOK heredoc body now == candidate md5",
              hashlib.md5(body.encode("utf-8")).hexdigest() == cand_md5)
    else:
        log("STATUS: RUNBOOK Step 6 heredoc already equals candidate (no change).")

    # Budget protection section must state the three required facts.
    with open(RUNBOOK_PATH, "r", encoding="utf-8", newline="") as fh:
        rb = fh.read()
    sec_start = rb.find("## Budget protection")
    sec_end = rb.find("\n## ", sec_start + 1)
    section = rb[sec_start:sec_end] if sec_start != -1 else ""
    low = section.lower()
    check("budget section: gate runs AFTER cache lookup",
          "after the cache lookup" in low)
    check("budget section: hash cap at gate -> per_image_retry_budget",
          "per_image_retry_budget" in section and "hash" in low)
    check("budget section: daily cap enforced atomically at consume",
          "atomic" in low and "consume" in low)
    return all(ok for _, ok in RESULTS if _ in (
        "RUNBOOK heredoc body == candidate md5",
        "RUNBOOK heredoc body now == candidate md5",
        "budget section: gate runs AFTER cache lookup",
        "budget section: hash cap at gate -> per_image_retry_budget",
        "budget section: daily cap enforced atomically at consume",
    ))


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def read_runbook_heredoc():
    with open(RUNBOOK_PATH, "r", encoding="utf-8", newline="") as fh:
        lines = fh.read().split("\n")
    si = None
    for i, ln in enumerate(lines):
        if ln.strip() == "cat > /root/ai-check/app.py << 'EOF'":
            si = i
            break
    if si is None:
        return None
    for i in range(si + 1, len(lines)):
        if lines[i].strip() == "EOF":
            return "\n".join(lines[si + 1:i])
    return None


def selfcheck():
    """Offline validation: treat the RUNBOOK heredoc as the final candidate."""
    candidate = read_runbook_heredoc()
    if candidate is None:
        log("SELFCHECK FAIL: heredoc not found")
        return EXIT_VALIDATE
    log("STATUS: selfcheck candidate %d bytes md5=%s"
        % (len(candidate.encode("utf-8")),
           hashlib.md5(candidate.encode("utf-8")).hexdigest()))
    RESULTS.clear()
    audit_ok = audit(candidate)
    code, detail = local_compile(candidate)
    log("Local py_compile -> exit %d%s" % (code, (": " + detail) if detail else ""))
    RESULTS.clear()
    logic_ok = logic_test(candidate)
    log("SELFCHECK: audit=%s compile_exit=%d logic=%s"
        % ("PASS" if audit_ok else "FAIL", code,
           "PASS" if logic_ok else "FAIL"))
    return EXIT_OK if (audit_ok and code == 0 and logic_ok) else EXIT_VALIDATE


def revtest():
    """Offline: reverse-engineer the defective v2 from the RUNBOOK final, then
    confirm build_candidate(v2) reproduces the final byte-for-byte."""
    final = read_runbook_heredoc()
    if final is None:
        log("REVTEST FAIL: heredoc not found")
        return EXIT_VALIDATE

    old_def = '''def budget_gate(resource):
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
        conn.close()'''

    old_gate = (
        '    resource = request.form.get("src") or ""\n'
        '    if not resource and request.form.get("url"):\n'
        '        resource = request.form["url"]\n'
        '    resource = resource.strip()[:2000]\n'
        '    reason = budget_gate(resource)\n'
        '    if reason:\n'
        '        # never break the client pipeline; the cached snapshot stays available\n'
        '            return jsonify(error="rate_limited", reason=reason), 429'
    )

    v2 = final.replace(NEW_DEF + "\n", old_def + "\n", 1)
    v2 = v2.replace(CACHED_RETURN_STMT + "\n\n" + GATE_BLOCK, CACHED_RETURN_STMT, 1)
    v2 = v2.replace("\n\n" + CACHE_GET_LINE, "\n\n" + old_gate + "\n\n" + CACHE_GET_LINE, 1)

    check("revtest: v2 construct differs from final",
          hashlib.md5(v2.encode("utf-8")).hexdigest()
          != hashlib.md5(final.encode("utf-8")).hexdigest())
    try:
        rebuilt = build_candidate(v2)
    except ValueError as exc:
        check("revtest: build_candidate(v2) did not raise (%s)" % exc, False)
        return EXIT_VALIDATE
    same = hashlib.md5(rebuilt.encode("utf-8")).hexdigest() == \
        hashlib.md5(final.encode("utf-8")).hexdigest()
    check("revtest: build_candidate(v2) reproduces final byte-for-byte", same)
    log("REVTEST: %s" % ("PASS" if same else "FAIL"))
    return EXIT_OK if same else EXIT_VALIDATE


def _first_diff(a, b):
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    if len(a) != len(b):
        return n
    return -1


def revtest_debug():
    final = read_runbook_heredoc()
    old_def = '''def budget_gate(resource):
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
        conn.close()'''
    old_gate = (
        '    resource = request.form.get("src") or ""\n'
        '    if not resource and request.form.get("url"):\n'
        '        resource = request.form["url"]\n'
        '    resource = resource.strip()[:2000]\n'
        '    reason = budget_gate(resource)\n'
        '    if reason:\n'
        '        # never break the client pipeline; the cached snapshot stays available\n'
        '            return jsonify(error="rate_limited", reason=reason), 429'
    )
    v2 = final.replace(NEW_DEF + "\n", old_def + "\n", 1)
    v2 = v2.replace(CACHED_RETURN_STMT + "\n\n" + GATE_BLOCK, CACHED_RETURN_STMT, 1)
    v2 = v2.replace("\n\n" + CACHE_GET_LINE, "\n\n" + old_gate + "\n\n" + CACHE_GET_LINE, 1)
    rebuilt = build_candidate(v2)
    i = _first_diff(rebuilt, final)
    log("DEBUG: len(final)=%d len(rebuilt)=%d first_diff_index=%d"
        % (len(final), len(rebuilt), i))
    if i != -1:
        log("DEBUG: final line=%d  rebuilt line=%d"
            % (final.count("\n", 0, i) + 1, rebuilt.count("\n", 0, i) + 1))
        log("DEBUG: final trailing 1 char is %s, rebuilt is %s (repr-safe widths)"
            % ("\\n" if final.endswith("\n") else "n", 
               "\\n" if rebuilt.endswith("\n") else "n"))
    return EXIT_OK


def inspect_remote(host, user, password):
    client = None
    try:
        client = ssh_connect(host, user, password)
        raw = sftp_read_bytes(client, REMOTE_PATH)
        content = raw.decode("utf-8")
        log("INSPECT: %s %d bytes md5=%s" % (REMOTE_PATH, len(raw),
              hashlib.md5(raw).hexdigest()))
        final = read_runbook_heredoc()
        log("INSPECT: RUNBOOK final %d bytes md5=%s"
            % (len(final.encode("utf-8")),
               hashlib.md5(final.encode("utf-8")).hexdigest()))
        i = _first_diff(content, final)
        log("INSPECT: first_diff_vs_final=%d (line %d)"
            % (i, content.count("\n", 0, i) + 1 if i != -1 else -1))
        log("INSPECT: len(remote)-len(final)=%d" % (len(content) - len(final)))
        RESULTS.clear()
        audit(content)
        # structural specifics
        log("INSPECT: GATE_BLOCK count=%d" % content.count(GATE_BLOCK))
        log("INSPECT: CACHED_RETURN count=%d" % content.count(CACHED_RETURN_STMT))
        cb = function_block(content, "check") or ""
        log("INSPECT: gate(resource,h) call in check=%d" % cb.count("budget_gate(resource, h)"))
        log("INSPECT: old signature def=%s" % ("def budget_gate(resource):" in content))
        log("INSPECT: new signature def=%s" % ("def budget_gate(resource, h):" in content))
        log("INSPECT: CRLF present=%s" % ("\r\n" in content))
        log("INSPECT: remote trailing newlines=%d" % (len(content) - len(content.rstrip("\n"))))

        # md5 parity with the heredoc AS WRITTEN by `cat` (body + terminating newline).
        final_out = final + "\n"
        log("INSPECT: heredoc-output md5=%s equals remote=%s"
            % (hashlib.md5(final_out.encode("utf-8")).hexdigest(),
               hashlib.md5(final_out.encode("utf-8")).hexdigest()
               == hashlib.md5(raw).hexdigest()))

        # Remote compile (explicitly sanctioned by the task).
        code, out, err = ssh_run(
            client,
            "cd /root/ai-check && venv/bin/python -m py_compile %s" % REMOTE_PATH)
        log("INSPECT: remote py_compile exit=%s" % code)

        # GET /health only.
        try:
            resp = requests.get("http://%s/health" % host, timeout=10)
            log("INSPECT: GET /health -> HTTP %s %s" % (resp.status_code, resp.json()))
        except Exception as exc:  # noqa: BLE001
            log("INSPECT: health request failed: %s" % exc)

        code, detail = local_compile(content)
        log("INSPECT: local py_compile (remote text) -> exit %d%s"
            % (code, (": " + detail) if detail else ""))

        RESULTS.clear()
        logic_test(final)
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001
        log("INSPECT FAILED: %s" % exc)
        return EXIT_FAILED
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass


def rbcheck():
    """Offline, read-only: heredoc-output md5 + required budget-section facts."""
    with open(RUNBOOK_PATH, "r", encoding="utf-8", newline="") as fh:
        rb = fh.read()
    body = read_runbook_heredoc()
    out = (body + "\n") if body is not None else ""
    log("RBCHECK: heredoc-output %d bytes md5=%s"
        % (len(out.encode("utf-8")), hashlib.md5(out.encode("utf-8")).hexdigest()))
    sec_start = rb.find("## Budget protection")
    sec_end = rb.find("\n## ", sec_start + 1)
    section = rb[sec_start:sec_end] if sec_start != -1 else ""
    low = section.lower()
    check("RB: heredoc found", body is not None)
    check("RB: section present", bool(section))
    check("RB: gate runs AFTER cache lookup stated",
          "after the cache lookup" in low)
    check("RB: hash cap at gate -> per_image_retry_budget stated",
          "per_image_retry_budget" in section and "hash" in low)
    check("RB: daily cap enforced atomically at consume stated",
          "begin immediate" in low and "consume" in low)
    total = sum(1 for _, ok in RESULTS if ok)
    log("RBCHECK totals: %d/%d PASS" % (total, len(RESULTS)))
    return EXIT_OK if total == len(RESULTS) else EXIT_VALIDATE


def main():
    if "--rbcheck" in sys.argv:
        return rbcheck()
    if "--inspect" in sys.argv:
        env = load_env(ENV_PATH) if os.path.exists(ENV_PATH) else {}
        host = get_secret(env, "VULTR_SSH_HOST")
        user = get_secret(env, "VULTR_SSH_USER")
        password = get_secret(env, "VULTR_SSH_PASSWORD")
        if not (host and user and password):
            log("ABORT: missing .env values.")
            return EXIT_ENV
        return inspect_remote(host, user, password)
    if "--diffdebug" in sys.argv:
        return revtest_debug()
    if "--selfcheck" in sys.argv:
        return selfcheck()
    if "--revtest" in sys.argv:
        return revtest()

    if not os.path.exists(ENV_PATH):
        log("ABORT: .env not found.")
        return EXIT_ENV
    env = load_env(ENV_PATH)
    host = get_secret(env, "VULTR_SSH_HOST")
    user = get_secret(env, "VULTR_SSH_USER")
    password = get_secret(env, "VULTR_SSH_PASSWORD")
    missing = [n for n, v in (("HOST", host), ("USER", user), ("PASSWORD", password)) if not v]
    if missing:
        log("ABORT: missing .env values for: %s" % ", ".join(missing))
        return EXIT_ENV

    client = None
    try:
        log("STATUS: connecting (patch-only; no restart/kill/signal).")
        client = ssh_connect(host, user, password)

        raw = sftp_read_bytes(client, REMOTE_PATH)
        content = raw.decode("utf-8")
        md5_before = hashlib.md5(raw).hexdigest()
        log("STATUS: fetched %s (%d bytes) md5_before=%s" % (REMOTE_PATH, len(raw), md5_before))
        if md5_before != EXPECTED_MD5_BEFORE:
            log("WARN: md5_before != expected %s (continuing; anchors are the guard)."
                % EXPECTED_MD5_BEFORE)

        # ---- BUILD CANDIDATE (both edits) -----------------------------
        try:
            candidate = build_candidate(content)
        except ValueError as exc:
            log("ABORT: anchor mismatch: %s. No changes made." % exc)
            return EXIT_VALIDATE
        log("STATUS: both edits applied (gate moved + budget_gate signature/cap).")
        log("STATUS: candidate %d bytes md5=%s"
            % (len(candidate.encode('utf-8')),
               hashlib.md5(candidate.encode('utf-8')).hexdigest()))

        # ---- AUDIT ----------------------------------------------------
        if not audit(candidate):
            log("ABORT: marker audit failed. No changes made.")
            return EXIT_VALIDATE

        # ---- LOCAL COMPILE -------------------------------------------
        code, detail = local_compile(candidate)
        log("Local py_compile -> exit %d%s" % (code, (": " + detail) if detail else ""))
        if code != 0:
            return EXIT_VALIDATE

        # ---- LOGIC TEST (isolated namespace) --------------------------
        RESULTS.clear()
        if not logic_test(candidate):
            log("ABORT: logic test failed. No changes made.")
            return EXIT_VALIDATE

        # ---- UPLOAD (patch-only; no new backup) -----------------------
        sftp_write(client, REMOTE_PATH, candidate)
        log("STATUS: uploaded candidate to %s (mode 0644)." % REMOTE_PATH)

        raw_after = sftp_read_bytes(client, REMOTE_PATH)
        md5_after = hashlib.md5(raw_after).hexdigest()
        cand_md5 = hashlib.md5(candidate.encode("utf-8")).hexdigest()
        if md5_after != cand_md5:
            log("ABORT: remote md5 %s != candidate md5 %s." % (md5_after, cand_md5))
            return EXIT_FAILED
        log("STATUS: re-read md5 matches candidate (%s)." % md5_after)

        # ---- REMOTE COMPILE ------------------------------------------
        code, out, err = ssh_run(
            client,
            "cd /root/ai-check && venv/bin/python -m py_compile %s" % REMOTE_PATH)
        detail = (err.strip() or out.strip())
        log("Remote py_compile -> exit %s%s" % (code, (": " + detail) if detail else ""))
        if code != 0:
            return EXIT_FAILED

        # ---- HEALTH (GET only) ---------------------------------------
        try:
            resp = requests.get("http://%s/health" % host, timeout=10)
            hcode, hdata = resp.status_code, resp.json()
        except Exception as exc:  # noqa: BLE001
            hcode, hdata = None, str(exc)
        log("GET /health -> HTTP %s %s" % (hcode, hdata))

        # ---- RUNBOOK PARITY ------------------------------------------
        RESULTS.clear()
        parity_ok = runbook_parity(candidate)
        log("RUNBOOK parity: %s" % ("PASS" if parity_ok else "FAIL"))

        log("")
        log("DONE v3 — dormant until manual restart: Ctrl+C then: "
            "cd /root/ai-check && venv/bin/python app.py")
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
