"""
Register a Windows Task Scheduler task to run digest.py daily at 6:00 AM.

Run once (no Administrator needed — it registers a task for your own user):
    python setup_task.py

The task is defined via XML so it can:
  - set the working directory to this project (relative paths resolve correctly)
  - run as your interactive user (so it can read your Claude Code login)
  - wake the computer if asleep, and run as soon as possible after a missed
    start (e.g. the machine was off at 6:00 AM)
"""

import getpass
import os
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

TASK_NAME = "RssDigest"
RUN_TIME = "06:00:00"


def _user_id() -> str:
    domain = os.environ.get("USERDOMAIN") or os.environ.get("COMPUTERNAME") or ""
    user = getpass.getuser()
    return f"{domain}\\{user}" if domain else user


def _build_xml(wake: bool) -> str:
    repo_dir = Path(__file__).resolve().parent
    python = sys.executable
    script = repo_dir / "digest.py"
    start = f"{date.today().isoformat()}T{RUN_TIME}"
    user = _user_id()
    # WakeToRun requires admin to set; it's optional (StartWhenAvailable still
    # runs the job when the machine next wakes if it was asleep at 6 AM).
    wake_line = "    <WakeToRun>true</WakeToRun>\n" if wake else ""

    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>rss-digest: daily RSS digest (summarize, publish, email)</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{start}</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
{wake_line}    <Enabled>true</Enabled>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{python}</Command>
      <Arguments>"{script}"</Arguments>
      <WorkingDirectory>{repo_dir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _register(wake: bool) -> subprocess.CompletedProcess:
    xml = _build_xml(wake)
    # schtasks wants a Unicode (UTF-16) XML file.
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".xml", delete=False, encoding="utf-16"
    )
    try:
        tmp.write(xml)
        tmp.close()
        cmd = ["schtasks", "/Create", "/TN", TASK_NAME, "/XML", tmp.name, "/F"]
        return subprocess.run(cmd, capture_output=True, text=True)
    finally:
        os.unlink(tmp.name)


def main() -> None:
    # Try with wake-from-sleep first; that flag needs admin, so on Access
    # Denied fall back to a version without it (no elevation required).
    result = _register(wake=True)
    wake = True
    if result.returncode != 0 and "denied" in (result.stderr + result.stdout).lower():
        result = _register(wake=False)
        wake = False

    if result.returncode == 0:
        print(f"Task '{TASK_NAME}' registered successfully — runs daily at {RUN_TIME[:5]}.")
        print("  Working dir : " + str(Path(__file__).resolve().parent))
        print("  Runs as     : " + _user_id() + " (only while logged on)")
        print("  Runs after a missed start (e.g. PC was asleep): yes")
        if wake:
            print("  Wakes the PC from sleep to run on time         : yes")
        else:
            print("  Wakes the PC from sleep to run on time         : NO")
            print("    -> To enable exact-time wake, re-run this from an")
            print("       Administrator shell:  python setup_task.py")
        print("\nVerify:  schtasks /Query /TN " + TASK_NAME)
        print("Remove:  schtasks /Delete /TN " + TASK_NAME + " /F")
        print("Test now: schtasks /Run /TN " + TASK_NAME)
    else:
        print("Failed to register task:")
        print((result.stderr or result.stdout).strip())
        sys.exit(1)


if __name__ == "__main__":
    main()
