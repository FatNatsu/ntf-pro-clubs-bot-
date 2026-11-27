"""Runs every test script against an isolated temporary database.

    python3 tests/run_all.py

No Discord connection is used: tests/fakes.py stands in for discord.py, and
each script points PRO_CLUBS_DB_PATH at a fresh temp file, so the real
database is never opened. Exit code is non-zero if any script fails."""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

scripts = sorted(f for f in os.listdir(HERE) if f.startswith("run_") and f.endswith(".py") and f != "run_all.py")
failed = []
for name in scripts:
    env = dict(os.environ)
    env.pop("PRO_CLUBS_DB_PATH", None)  # every script makes its own temp database
    proc = subprocess.run([sys.executable, "-I", os.path.join(HERE, name), REPO], capture_output=True, text=True, env=env)
    ok = proc.returncode == 0
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        failed.append(name)
        print((proc.stdout + proc.stderr)[-3000:])

print(f"\n{len(scripts) - len(failed)}/{len(scripts)} passed")
sys.exit(1 if failed else 0)
