from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .config import PUBLISH_FILES, REMOTE_SSH, ROOT


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("GIT_SSH_COMMAND", "ssh -o IdentitiesOnly=yes -o BatchMode=yes")
    return subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True)


def publish_html(output_dir: Path | None = None) -> None:
    root = Path(output_dir or ROOT)
    existing = [name for name in PUBLISH_FILES if (root / name).exists()]
    if not existing:
        print("没有可推送的 HTML")
        return
    add = _run(["git", "add", "--"] + existing, root)
    if add.returncode != 0:
        raise RuntimeError(add.stderr.strip() or "git add 失败")
    diff = _run(["git", "diff", "--cached", "--quiet"], root)
    if diff.returncode == 0:
        print("HTML 无变化，跳过推送")
        return
    commit = _run(["git", "commit", "-m", "自动更新幸存者排名"], root)
    if commit.returncode != 0:
        raise RuntimeError(commit.stderr.strip() or commit.stdout.strip() or "git commit 失败")
    push = _run(["git", "push", REMOTE_SSH, "HEAD:main"], root)
    if push.returncode != 0:
        raise RuntimeError(push.stderr.strip() or "git push 失败")
    print("已推送 HTML 到 GitHub")
