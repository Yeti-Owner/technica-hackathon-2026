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
"""

import hashlib
import os
import re
import sys
import time

import paramiko
import requests

# Patch-only mode: skip ALL restart logic (systemctl restart, rollback-restart,
# service status checks). The running process is left completely untouched.
NO_RESTART = "--no-restart" in sys.argv

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(REPO_ROOT, ".env")

REMOTE_PATH = "/root/ai-check/app.py"
REMOTE_BAK = "/root/ai-check/app.py.bak"
SERVICE = "ai-check"

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
