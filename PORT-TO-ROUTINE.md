# Porting the digest off the Windows scheduled task

Scoped 2026-08-18. Goal: run the 6 AM digest from a Claude Code cloud Routine so
it stops depending on Jamie's PC being awake.

**Why it matters:** over Aug 3–18 the scheduled task delivered 13 of 16 days.
Missing: Aug 3, Aug 5, Aug 12. A cloud job does not miss scattered days; a
machine-dependent one does.

## Verdict: viable, and easier than expected

The feared blocker was the Claude Code CLI. `summarizer.py` shells out to
`claude -p` with no API key, which on Windows works only because the scheduled
task runs as `InteractiveToken` and inherits an interactive login. That looked
unportable to an ephemeral container.

**It ports fine.** Tested in a live cloud container on 2026-08-18:

- `claude` is present at `/opt/node22/bin/claude`
- `claude -p "..." --output-format json` returned `is_error: false`, exit 0,
  with real API usage — **credentials are ambient, no interactive login needed**
- Python 3.11.15, `pip install feedparser` works
- Feed egress works: 5 of 6 sampled feeds returned 200, and the one failure was
  a 404 from a missing User-Agent in the *test*, not a proxy block. `fetcher.py`
  already sends `User-Agent: rss-digest/1.0`, so the pipeline is unaffected

So the pipeline runs largely as-is. **Do not** rewrite the summarizer to call an
API directly, and do not invert the design so the Routine's own session does the
summarizing — neither is necessary now.

## What actually has to change

Ranked by what stops a run dead.

### 1. The feed list is not in the repo — hard blocker

`opml/feedly.opml` is gitignored and absent from a fresh clone. `digest.py`
`sys.exit(1)`s without it, so a cloud run cannot start.

`feeds.yaml` (152 lines) exists, is committed, and its header says it replaces
the Feedly OPML export — **but no code reads it.** `fetcher.parse_opml` handles
OPML only, and no YAML parser is installed.

Two options:
- **Commit the OPML.** Fastest. Feed URLs are not secrets; check the file first
  for anything private before un-ignoring it.
- **Wire up `feeds.yaml`.** Cleaner and clearly the original intent. Needs
  `pyyaml` in `requirements.txt` and a `parse_feeds_yaml()` alongside
  `parse_opml()`.

### 2. Seen-article state is local-only — hard blocker

`seen_articles.json` (`{"last_run": ..., "seen": {id: date}}`, pruned to 30 days)
is gitignored and never committed. An ephemeral container starts empty every run,
so the whole 48-hour window looks new: **a duplicate digest every day, and the
full summarization cost re-paid.**

Fix: commit it. `publisher.push()` already commits and pushes, so add the state
file to the staged set. It is small and prunes itself.

### 3. `%#d` / `%#I` strftime — breaks on glibc

The Windows-only `#` no-pad flag appears in `mailer.py` (4×), `renderer.py`,
`digest.py`, and `templates/digest.html.j2` (2×). glibc wants `%-d` / `%-I`.
`SETUP.md:196` already acknowledges this.

**Do not simply swap to `%-d`** — that breaks the Windows copy, and during
cutover both may run. Use a small portable helper (`str(dt.day)`,
`dt.strftime('%I').lstrip('0')`) so one codebase works on both.

### 4. Timezone

No TZ is set anywhere; `datetime.now().astimezone()` drives the digest date and
the `docs/<date>.html` filename. A UTC container at 06:00 Central is already on
the next date, so the digest would be filed a day ahead. Set
`TZ=America/Chicago` for the run, or pass an explicit date.

### 5. Git push credentials

`publisher.py` runs bare `git push origin main` and relies on ambient credentials
(Windows Credential Manager locally). In the Routine the repo is attached with
push access, so the proxy handles auth — but the container still needs
`git config user.name` / `user.email` set, or the commit fails.

### 6. Email

`mailer.py` uses SMTP_SSL to Gmail with `GMAIL_APP_PASSWORD` from `.env`, which
is correctly gitignored and therefore absent in the container.

**Preferred: send via the session's Gmail connector instead**, so no app password
has to be stored in the cloud at all. Fallback is supplying the env vars to the
Routine, which means putting a real credential somewhere it currently isn't.

### 7. Scheduler

`setup_task.py` is Windows-only (schtasks + UTF-16 task XML) and is simply not
used. The Routine's cron replaces it:

| Period | Cron (UTC) | Local |
|---|---|---|
| CDT | `0 11 * * *` | 06:00 |
| CST | `0 12 * * *` | 06:00 |

Daily, so a UTC rollover only shifts the hour — no weekday problem.

## Cost — measure before committing

The trivial CLI test cost **$0.26**, almost all of it cache-creation on a ~42k
system prompt. `summarizer.py` makes one call per article, top 10 per category,
concurrency 4. If per-call overhead stays near that, a 40–50 article day is
**$10–13**, every day.

`--exclude-dynamic-system-prompt-sections` is already passed, which helps. But
measure a real run before turning this on daily. If it is expensive, batching
several articles per call is the obvious lever and a much smaller change than
re-architecting.

## Failure mode to fix while in here

When the CLI exhausts its 3 retries, `summarizer.py` falls back to truncated raw
text. The digest still renders and still sends — it just has no AI summaries.
**A broken summarizer produces a digest that looks fine.** That is the same class
of silent failure the other Routines were rebuilt to eliminate; the run should
say so loudly instead.

## Suggested order

1. Commit the feed list (or wire `feeds.yaml`) and `seen_articles.json`
2. Portable strftime helper
3. Run `digest.py --dry-run` manually in a cloud session — it skips push, email
   and the state write, so it is safe and proves fetch + summarize + render
4. Measure cost from that run
5. Create the Routine at `0 11 * * *`, bound to the Chief of Staff session
6. Run both for a few days, compare output
7. Disable the Windows task (see below)

---

# Local cutover — stopping the PC from running it twice

Do this **only after** the Routine has produced a correct digest on its own for a
few days. Until then, both running is the safety net.

Two things run daily and both must be dealt with, or you get duplicate digests,
duplicate emails, and racing pushes to `main`.

## 1. Disable the scheduled task

The task is named **`RssDigest`** and fires at **06:00:00** local.

In an elevated PowerShell or cmd:

```powershell
# Check what it is doing now
schtasks /Query /TN RssDigest /V /FO LIST

# Preferred: disable, keeping the definition so it can be re-enabled
schtasks /Change /TN RssDigest /DISABLE

# Confirm — Scheduled Task State should read Disabled
schtasks /Query /TN RssDigest /FO LIST | findstr /I "State"
```

**Disable rather than delete.** If the cloud run turns out to be too expensive or
unreliable, re-enabling is one command:

```powershell
schtasks /Change /TN RssDigest /ENABLE
```

Only delete once you are certain: `schtasks /Delete /TN RssDigest /F`.

## 2. Handle the local clone

Even with the task off, `C:\Github\rss-digest` still holds:

- `.env` — the only copy of `GMAIL_APP_PASSWORD`. **Do not delete it.** Keep it
  as the credential of record, and as what you need to re-enable locally.
- `seen_articles.json` — once state is committed to the repo, the local file and
  the committed one will diverge, and whichever side runs last wins. With the
  task disabled the local copy simply goes stale, which is harmless. If you ever
  re-enable, `git pull` first so it picks up the cloud state.

**Pull before any manual run.** The Routine pushes `docs/` and the state file to
`main`, so a stale local clone will conflict or clobber. If you want to run it by
hand after cutover:

```powershell
cd C:\Github\rss-digest
git pull
python digest.py --dry-run   # safe: no push, no email, no state write
```

## 3. Watch for the double-push window

While both are running, they commit to the same branch within minutes of each
other. `publisher.push()` does not pull first, so the loser gets a rejected push
and that run's digest never reaches Pages — while the email still goes out,
linking to a page that does not exist yet.

If you see that during the overlap, it is expected. It disappears when the task
is disabled. If you would rather avoid it entirely, disable the task the same day
the Routine goes live and accept a short unmonitored period instead.

## 4. What to verify after cutover

- An email arrives at ~06:00 Central
- `https://<pages-base>/<date>.html` resolves — Pages has to rebuild after the push
- The newest commit in this repo reads `digest: <today>`
- Articles are not repeats of yesterday's, which is the tell that state is working
