#!/usr/bin/env python3
"""
One-off ops tool: make PNG metadata parsing NON-FATAL in /root/ai-check/app.py
on the Vultr box.

Bug: a client POST to /check crashed with
    ValueError: Decompressed data too large for PngImagePlugin.MAX_TEXT_CHUNK
raised from `texts.update(getattr(img, "text", {}) or {})` inside local_check().
Pillow raises when a PNG zTXt chunk decompresses larger than its safe cap.

Fix: wrap the PNG metadata parse in try/except so the image falls through to the
SightEngine fallback instead of 500-ing.

This is a LOCAL-ONLY tool. It never installs anything on the server and never
prints or logs any secret value. The only remote changes it makes are:
  * /root/ai-check/app.py        (patched in place)
  * /root/ai-check/app.py.bak    (one-time backup, created with `cp -n`)

It NEVER starts, restarts, kills or signals any process.

Run with the project venv:
    .venv\\Scripts\\python.exe tools\\vultr_patch_image_safety.py
"""

import hashlib
import os
import re
import sys

import paramiko

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(REPO_ROOT, ".env")

REMOTE_PATH = "/root/ai-check/app.py"
REMOTE_BAK = "/root/ai-check/app.py.bak"

CONNECT_PATCHED = "sqlite3.connect(DB_PATH, timeout=30)"
CONNECT_ANY = "sqlite3.connect("
PRAGMA_MARK = "PRAGMA journal_mode=WAL"
MARKER = "# image-safety: chunk failures tolerated"
LOCAL_CALL = "score, source = local_check(data)"
SIGHTENGINE_CALL = "sightengine_check(data)"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ENV = 2
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
# Code analysis / patching
# --------------------------------------------------------------------------
def build_pair_re(nl):
    return re.compile(
        r"^(?P<indent>[ \t]*)texts = dict\(img\.info or \{\}\)" + nl +
        r"(?P=indent)texts\.update\(getattr\(img, \"text\", \{\}\) or \{\}\)",
        re.MULTILINE,
    )


def build_local_call_re(nl):
    return re.compile(
        r"^(?P<indent>[ \t]*)score, source = local_check\(data\)[ \t]*$",
        re.MULTILINE,
    )


def apply_patch(content, nl):
    """Return (patched_content, before_block, after_block) for CHANGE 1+2."""
    pair_re = build_pair_re(nl)
    call_re = build_local_call_re(nl)

    pair_matches = list(pair_re.finditer(content))
    call_matches = list(call_re.finditer(content))
    if len(pair_matches) != 1:
        raise ValueError("expected exactly 1 vulnerable pair, found %d" % len(pair_matches))
    if len(call_matches) != 1:
        raise ValueError("expected exactly 1 local_check call, found %d" % len(call_matches))

    pm = pair_matches[0]
    indent = pm.group("indent")
    before1 = pm.group(0)
    after1 = nl.join([
        indent + "try:",
        indent + "    texts = dict(img.info or {})",
        indent + "    texts.update(getattr(img, \"text\", {}) or {})",
        indent + "except Exception:",
        indent + "    " + MARKER,
        indent + "    return None, None",
    ])
    content = content[:pm.start()] + after1 + content[pm.end():]

    cm = call_re.search(content)
    indent2 = cm.group("indent")
    before2 = cm.group(0)
    after2 = nl.join([
        indent2 + "try:",
        indent2 + "    score, source = local_check(data)",
        indent2 + "except Exception as local_error:",
        indent2 + "    print(\"local_check failed:\", repr(local_error))",
        indent2 + "    score, source = None, None",
    ])
    content = content[:cm.start()] + after2 + content[cm.end():]

    return content, before1, after1, before2, after2


def sightengine_inside_score_none(content, nl):
    """True if sightengine_check(data) appears inside the `if score is None:` block."""
    lines = content.split(nl)
    idx = None
    for i, ln in enumerate(lines):
        if re.match(r"^[ \t]*if score is None:", ln):
            idx = i
            break
    if idx is None:
        return False
    base_indent = len(lines[idx]) - len(lines[idx].lstrip())
    for j in range(idx + 1, len(lines)):
        stripped = lines[j].strip()
        if not stripped:
            continue
        cur_indent = len(lines[j]) - len(lines[j].lstrip())
        if cur_indent <= base_indent:
            break
        if SIGHTENGINE_CALL in lines[j]:
            return True
    return False


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
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
    md5_before = None
    md5_after = None
    bak_preexisting = False
    write_occurred = False
    try:
        log("Connecting over SSH to %s ..." % host)
        client = ssh_connect(host, user, password)
        log("SSH connected.")

        # ---- STEP 2: PROCESS STATE (OBSERVE ONLY) ---------------------
        log("")
        log("--- Process state (observe only) ---")
        _, ps_out, _ = ssh_run(client, "ps aux | grep '[a]pp.py' | head -3")
        ps_lines = [ln for ln in ps_out.strip().splitlines() if ln.strip()]
        if ps_lines:
            for line in ps_lines:
                log("    ps: %s" % line)
            proc_state = "still running"
        else:
            log("    ps: (no app.py process found)")
            proc_state = "not running"
        _, ss_out, _ = ssh_run(client, "ss -ltnp | grep ':80 ' || echo NO_PORT80_LISTENER")
        for line in ss_out.strip().splitlines():
            log("    ss: %s" % line)
        log("Process state observed: app.py %s (no action taken)." % proc_state)

        # ---- STEP 3: FETCH + VALIDATE ---------------------------------
        try:
            raw = sftp_read_bytes(client, REMOTE_PATH)
            content = raw.decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            log("ABORT: could not read %s: %s. No changes made." % (REMOTE_PATH, exc))
            return EXIT_VALIDATE
        md5_before = hashlib.md5(raw).hexdigest()
        log("")
        log("Fetched %s (%d bytes). md5=%s" % (REMOTE_PATH, len(raw), md5_before))

        nl = "\r\n" if "\r\n" in content else "\n"

        # (e) idempotency marker
        if MARKER in content:
            log("Marker %r already present -> ALREADY_PATCHED." % MARKER)
            code, out, err = ssh_run(
                client, "test -f %s && echo present || echo absent" % REMOTE_BAK
            )
            bak_present = out.strip() == "present"
            log("")
            log("=== SUMMARY (ALREADY_PATCHED) ===")
            log("Process state: %s (no action taken)." % proc_state)
            log("Patch: not applied (marker already present).")
            log("md5 before: %s" % md5_before)
            log("md5 after : %s (unchanged)" % md5_before)
            log("Backup: %s" % ("present" if bak_present else "ABSENT"))
            log("No process was started, restarted, killed or signaled; "
                "no server file other than app.py (+ its backup) was touched.")
            return EXIT_OK

        # (a) sqlite3.connect forms
        connect_any = content.count(CONNECT_ANY)
        connect_patched = content.count(CONNECT_PATCHED)
        # (b) pragma
        pragma_count = content.count(PRAGMA_MARK)
        # (c) vulnerable pair
        pair_re = build_pair_re(nl)
        pair_count = len(pair_re.findall(content))
        # (d) local_check call
        call_re = build_local_call_re(nl)
        call_count = len(call_re.findall(content))

        log("")
        log("--- Validation ---")
        log("  sqlite3.connect( total            = %d (expected 3)" % connect_any)
        log("  sqlite3.connect(DB_PATH, timeout=30) = %d (expected 3)" % connect_patched)
        log("  PRAGMA journal_mode=WAL           = %d (expected 1)" % pragma_count)
        log("  vulnerable pair                   = %d (expected 1)" % pair_count)
        log("  score, source = local_check(data) = %d (expected 1)" % call_count)

        problems = []
        if connect_any != 3 or connect_patched != 3:
            problems.append(
                "sqlite3.connect forms: total=%d patched=%d (expected 3/3; "
                "any other sqlite3.connect form is a hard abort)" % (connect_any, connect_patched)
            )
        if pragma_count != 1:
            problems.append("PRAGMA journal_mode=WAL count=%d (expected 1)" % pragma_count)
        if pair_count != 1:
            problems.append("vulnerable pair count=%d (expected 1)" % pair_count)
        if call_count != 1:
            problems.append("local_check call count=%d (expected 1)" % call_count)
        if problems:
            log("ABORT: validation failed:")
            for p in problems:
                log("  - %s" % p)
            log("No changes made.")
            return EXIT_VALIDATE
        log("Validation passed.")

        # ---- STEP 4: BACKUP (never overwrite) -------------------------
        code, out, err = ssh_run(
            client, "test -f %s && echo present || echo absent" % REMOTE_BAK
        )
        bak_preexisting = out.strip() == "present"
        code, out, err = ssh_run(client, "cp -n %s %s" % (REMOTE_PATH, REMOTE_BAK))
        log("")
        log("Backup: cp -n %s %s -> exit %s" % (REMOTE_PATH, REMOTE_BAK, code))
        code, out, err = ssh_run(
            client, "test -f %s && echo present || echo absent" % REMOTE_BAK
        )
        bak_present = out.strip() == "present"
        log("Backup file %s: %s%s" % (
            REMOTE_BAK,
            "present" if bak_present else "ABSENT",
            " (pre-existed this task; not overwritten)" if bak_preexisting else " (created now)",
        ))

        # ---- STEP 5: EDIT IN MEMORY -----------------------------------
        try:
            patched, before1, after1, before2, after2 = apply_patch(content, nl)
        except ValueError as exc:
            log("ABORT: %s. No changes made." % exc)
            return EXIT_VALIDATE

        log("")
        log("--- CHANGE 1 (local_check, PNG metadata parse) before -> after ---")
        for ln in before1.split(nl):
            log("- %s" % ln)
        for ln in after1.split(nl):
            log("+ %s" % ln)
        log("--- CHANGE 2 (check, local_check call) before -> after ---")
        for ln in before2.split(nl):
            log("- %s" % ln)
        for ln in after2.split(nl):
            log("+ %s" % ln)
        log("-----------------------------------------------------------------")

        # ---- STEP 6: UPLOAD -------------------------------------------
        try:
            sftp_write(client, REMOTE_PATH, patched)
            write_occurred = True
        except Exception as exc:  # noqa: BLE001
            log("ABORT: upload failed: %s. Attempting rollback from backup ..." % exc)
            ssh_run(client, "cp %s %s" % (REMOTE_BAK, REMOTE_PATH))
            log("Rollback (file only, no restart) attempted. FAILED.")
            return EXIT_FAILED
        log("Uploaded patched file to %s (mode 0644)." % REMOTE_PATH)

        # ---- STEP 7: POST-VERIFY --------------------------------------
        raw2 = sftp_read_bytes(client, REMOTE_PATH)
        content2 = raw2.decode("utf-8")
        md5_after = hashlib.md5(raw2).hexdigest()

        def grep_count(pattern):
            code, out, err = ssh_run(
                client, "grep -c %s %s" % (shell_quote(pattern), REMOTE_PATH)
            )
            try:
                return int(out.strip() or "0")
            except ValueError:
                return -1

        g_timeout = grep_count("timeout=30")
        g_pragma = grep_count("PRAGMA journal_mode")
        g_marker = grep_count(MARKER)
        g_localfail = grep_count("local_check failed:")
        g_update = grep_count('texts.update(getattr(img, "text", {}) or {})')
        g_sight = grep_count(SIGHTENGINE_CALL)
        sight_inside = sightengine_inside_score_none(content2, nl)

        log("")
        log("--- Post-verify (SFTP read-back + SSH grep) ---")
        log("  grep -c \"timeout=30\"                                  = %d (expected 3)" % g_timeout)
        log("  grep -c \"PRAGMA journal_mode\"                         = %d (expected 1)" % g_pragma)
        log("  grep -c \"%s\" = %d (expected 1)" % (MARKER, g_marker))
        log("  grep -c \"local_check failed:\"                         = %d (expected 1)" % g_localfail)
        log("  grep -c 'texts.update(getattr(img, \"text\", {}) or {})' = %d (expected 1)" % g_update)
        log("  grep -c \"sightengine_check(data)\"                     = %d (expected 1)" % g_sight)
        log("  sightengine_check(data) inside `if score is None:`     = %s" % sight_inside)
        log("  md5 after = %s" % md5_after)

        ok = (
            g_timeout == 3
            and g_pragma == 1
            and g_marker == 1
            and g_localfail == 1
            and g_update == 1
            and g_sight == 1
            and sight_inside
            and bak_present
        )

        log("")
        if ok:
            log("=== SUMMARY (OK) ===")
            log("Process state: %s (no action taken)." % proc_state)
            log("Patch applied: PNG metadata parse wrapped in try/except; "
                "local_check call wrapped in try/except.")
            log("md5 before: %s" % md5_before)
            log("md5 after : %s" % md5_after)
            log("Backup: present (%s)%s" % (
                REMOTE_BAK, " [pre-existed this task]" if bak_preexisting else " [created now]"))
            log("No process was started, restarted, killed or signaled; "
                "no server file other than app.py (+ its backup) was touched.")
            return EXIT_OK

        log("=== SUMMARY (FAILED) ===")
        log("Process state: %s (no action taken)." % proc_state)
        log("Post-verify did not confirm all edits.")
        log("md5 before: %s" % md5_before)
        log("md5 after : %s" % md5_after)
        log("No process was started, restarted, killed or signaled.")
        return EXIT_FAILED

    except Exception as exc:  # noqa: BLE001
        log("FAILED: unexpected error: %s" % exc)
        log("Write occurred: %s" % write_occurred)
        return EXIT_FAILED
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass


def shell_quote(s):
    return "'" + s.replace("'", "'\\''") + "'"


if __name__ == "__main__":
    sys.exit(main())
