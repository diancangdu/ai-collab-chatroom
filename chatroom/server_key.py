#!/usr/bin/env python3
"""Extract the OpenCode desktop sidecar server credentials (stdout only).

Reads the OpenCode.exe process environment block via
NtQueryInformationProcess + ReadProcessMemory (x64), extracts
OPENCODE_SERVER_USERNAME / OPENCODE_SERVER_PASSWORD and the dynamic loopback
listen port. Credentials are NEVER written to disk or chat — stdout only,
meant to be consumed in-memory (即调即用) by wake_relay.

Usage:
  python server_key.py            # stdout: {"username":..,"password":..,"port":..}
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chatutil  # noqa: E402

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll")

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
ProcessBasicInformation = 0

class PROCESS_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("Reserved1", ctypes.c_void_p),
        ("PebBaseAddress", ctypes.c_void_p),
        ("Reserved2", ctypes.c_void_p * 2),
        ("UniqueProcessId", ctypes.c_void_p),
        ("Reserved3", ctypes.c_void_p),
    ]


def read_mem(hproc, addr, size):
    buf = ctypes.create_string_buffer(size)
    read = ctypes.c_size_t(0)
    if not k32.ReadProcessMemory(hproc, ctypes.c_void_p(addr), buf, size, ctypes.byref(read)):
        raise OSError("ReadProcessMemory failed at 0x%x" % addr)
    return buf.raw[: read.value]


def read_u64(hproc, addr):
    return int.from_bytes(read_mem(hproc, addr, 8), "little")


def get_env_block(hproc, peb):
    # x64: PEB->ProcessParameters at offset 0x20
    params = read_u64(hproc, peb + 0x20)
    # RTL_USER_PROCESS_PARAMETERS: Environment at 0x80, EnvironmentSize at 0x3F0
    env_addr = read_u64(hproc, params + 0x80)
    env_size = read_u64(hproc, params + 0x3F0)
    if env_addr == 0 or env_size == 0 or env_size > 8 * 1024 * 1024:
        raise OSError("bad environment block (addr=0x%x size=%d)" % (env_addr, env_size))
    raw = read_mem(hproc, env_addr, env_size)
    return raw.decode("utf-16-le", errors="ignore")


def parse_env(blob):
    env = {}
    for entry in blob.split("\x00"):
        if not entry or "=" not in entry:
            continue
        key, _, value = entry.partition("=")
        if key:
            env[key] = value
    return env


def find_main_pid():
    """Return the PID of the OpenCode main (browser) process."""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-CimInstance Win32_Process -Filter \"Name='OpenCode.exe'\" | "
         "Where-Object { $_.CommandLine -notmatch '--type=' }).ProcessId"],
        capture_output=True, text=True, timeout=15,
    )
    pids = [int(x) for x in out.stdout.split() if x.strip().isdigit()]
    if not pids:
        raise OSError("no OpenCode main process found")
    return pids[0]


def find_all_pids():
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-CimInstance Win32_Process -Filter \"Name='OpenCode.exe'\").ProcessId"],
        capture_output=True, text=True, timeout=15,
    )
    return [int(x) for x in out.stdout.split() if x.strip().isdigit()]


def read_env_of(pid):
    hproc = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not hproc:
        return {}
    try:
        pbi = PROCESS_BASIC_INFORMATION()
        ret = ntdll.NtQueryInformationProcess(
            hproc, ProcessBasicInformation, ctypes.byref(pbi), ctypes.sizeof(pbi), None)
        if ret != 0:
            return {}
        return parse_env(get_env_block(hproc, pbi.PebBaseAddress))
    except Exception:
        return {}
    finally:
        k32.CloseHandle(hproc)


def find_listen_port(pid):
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-NetTCPConnection -State Listen -OwningProcess %d -ErrorAction SilentlyContinue | "
         "Where-Object { $_.LocalAddress -eq '127.0.0.1' } | Select-Object -ExpandProperty LocalPort" % pid],
        capture_output=True, text=True, timeout=15,
    )
    ports = sorted({int(x) for x in out.stdout.split() if x.strip().isdigit()})
    return ports


def main():
    parser = argparse.ArgumentParser(description="Extract OpenCode sidecar server credentials (stdout only, never persisted)")
    parser.add_argument("--json", action="store_true", help="output single-line JSON (default: pretty)", )
    args = parser.parse_args()

    pid = find_main_pid()
    username = password = None
    cred_pid = None
    for cand in find_all_pids():
        env = read_env_of(cand)
        if env.get("OPENCODE_SERVER_PASSWORD"):
            username = env.get("OPENCODE_SERVER_USERNAME")
            password = env.get("OPENCODE_SERVER_PASSWORD")
            cred_pid = cand
            break
    if not password:
        raise OSError("credentials not found in any OpenCode process environment")
    ports = find_listen_port(cred_pid) or find_listen_port(pid)

    cred = {
        "pid": pid,
        "cred_pid": cred_pid,
        "username": username,
        "password": password,
        "port": ports[0] if ports else None,
        "ts": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
    }
    if args.json:
        print(json.dumps(cred, ensure_ascii=False))
    else:
        print(json.dumps(cred, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
