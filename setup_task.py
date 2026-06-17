"""
Register a Windows Task Scheduler task to run digest.py daily at 6:00 AM.
Run once as Administrator:  python setup_task.py
"""

import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    script = Path(__file__).parent / "digest.py"
    python = sys.executable

    task_name = "RssDigest"
    cmd = [
        "schtasks", "/Create",
        "/TN", task_name,
        "/TR", f'"{python}" "{script}"',
        "/SC", "DAILY",
        "/ST", "06:00",
        "/RL", "HIGHEST",
        "/F",   # overwrite if exists
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"Task '{task_name}' registered successfully.")
        print("It will run daily at 6:00 AM using:")
        print(f"  Python:  {python}")
        print(f"  Script:  {script}")
        print("\nTo remove the task later:")
        print(f"  schtasks /Delete /TN {task_name} /F")
    else:
        print("Failed to register task:")
        print(result.stderr)
        print("\nTry running this script as Administrator.")
        sys.exit(1)


if __name__ == "__main__":
    main()
