import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

FAKE = Path(__file__).parent / "fake_reviewer.py"

SERVICE = '''\
def is_adult(age):
    if age >= 18:
        return True
    return False


def apply_fee(total, vip):
    if not vip and total > 100:
        return total + 1
    return total


def query(db, name):
    return db.execute("SELECT * FROM t WHERE name = '" + name + "'")  # SUSPICIOUS


def merge(a, b):
    result = dict(a)
    for key in b:
        if key != "id":
            result[key] = b[key]
    return result
'''

UTIL = '''\
def window(items, size):
    out = []
    for i in range(len(items) - 1):  # MAYBE
        out.append(items[i:i + size])
    return out


def clamp(value, low, high):
    if value < low:
        return low
    if value > high:
        return high
    return value
'''


def git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          check=True, capture_output=True, text=True)
    return proc.stdout


def write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """Reviewer spec for the fake reviewer, with a fresh run counter and a private coinflip home."""
    monkeypatch.setenv("FAKE_STATE", str(tmp_path / "fake-state"))
    monkeypatch.setenv("FAKE_MODE", "ok")
    monkeypatch.setenv("COINFLIP_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("FAKE_ORIG", raising=False)
    return f'cmd:"{sys.executable}" "{FAKE}"'


@pytest.fixture
def project(tmp_path) -> Path:
    """A small committed git project with a few planted markers for the fake reviewer."""
    root = tmp_path / "myproject"
    write(root, "src/service.py", SERVICE)
    write(root, "src/util.py", UTIL)
    write(root, "README.md", "# demo\n")
    write(root, "node_modules/x/index.js", "function f() { return 1 == 2; }\n")
    write(root, ".gitignore", "node_modules/\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root
