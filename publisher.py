import subprocess
from pathlib import Path


def _run(cmd: list[str], cwd: str, check: bool = True) -> None:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0 and check:
        raise RuntimeError(f"git command failed: {' '.join(cmd)}\n{result.stderr}")


def push(repo_dir: str, date_str: str, message: str | None = None) -> None:
    """Stage docs/, commit, and push to origin main.

    `message` overrides the default "digest: <date>" subject. It exists so a
    run that produced no new articles can still leave a commit behind — a
    heartbeat. Without one, a quiet day and a dead scheduler are byte-identical
    from the outside, which is exactly how ten missed days went unnoticed.
    """
    _run(["git", "add", "docs/"], cwd=repo_dir)
    # State must travel with the repo: a cloud run starts from a fresh
    # clone, and without committed state every article looks new again.
    _run(["git", "add", "--", "seen_articles.json"], cwd=repo_dir, check=False)

    # Check if there's actually anything to commit
    status = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=repo_dir,
        capture_output=True,
    )
    if status.returncode == 0:
        # Nothing staged
        return

    _run(["git", "commit", "-m", message or f"digest: {date_str}"], cwd=repo_dir)
    _run(["git", "push", "origin", "main"], cwd=repo_dir)
