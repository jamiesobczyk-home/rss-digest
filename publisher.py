import subprocess
from pathlib import Path


def _run(cmd: list[str], cwd: str) -> None:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git command failed: {' '.join(cmd)}\n{result.stderr}")


def push(repo_dir: str, date_str: str) -> None:
    """Stage docs/, commit, and push to origin main."""
    _run(["git", "add", "docs/"], cwd=repo_dir)

    # Check if there's actually anything to commit
    status = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=repo_dir,
        capture_output=True,
    )
    if status.returncode == 0:
        # Nothing staged
        return

    _run(["git", "commit", "-m", f"digest: {date_str}"], cwd=repo_dir)
    _run(["git", "push", "origin", "main"], cwd=repo_dir)
