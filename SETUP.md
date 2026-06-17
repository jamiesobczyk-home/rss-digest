# rss-digest Setup Guide

A daily RSS digest that posts to GitHub Pages and emails you a summary at 6 AM.

---

## Prerequisites

- Python 3.10+
- Git installed and configured (`git config --global user.name` and `user.email`)
- A GitHub account
- A Gmail account with 2-Step Verification enabled
- Claude Code installed and logged in (the `claude` CLI). AI summaries run
  through it in headless mode using your existing login — no API key needed.

---

## Step 1 — Install Python dependencies

```
cd C:\Github\rss-digest
pip install -r requirements.txt
```

---

## Step 2 — Export your Feedly OPML

1. Open Feedly in your browser
2. Click your profile icon (bottom-left) → **Organize**
3. Click **Export OPML** (top-right of the Organize page)
4. Save the downloaded file as `opml\feedly.opml` inside this project folder

Re-export whenever you add, remove, or reorganize feeds.

---

## Step 3 — Create a GitHub repo for GitHub Pages

1. Go to github.com → **New repository**
2. Name it `rss-digest`
3. Set visibility to **Public** (required for free GitHub Pages)
4. Do **not** initialize with README
5. Click **Create repository**

Then initialize git and push this project:

```
cd C:\Github\rss-digest
git init
git remote add origin https://github.com/YOUR-USERNAME/rss-digest.git
git add .
git commit -m "initial commit"
git push -u origin main
```

6. In the GitHub repo, go to **Settings → Pages**
7. Under **Source**, select branch `main`, folder `/docs`
8. Click **Save**
9. After a minute, your site will be live at `https://YOUR-USERNAME.github.io/rss-digest`

---

## Step 4 — Make sure Claude Code is logged in

AI summaries run through your local Claude Code CLI in headless mode, so no
Anthropic API key is required — it uses whatever account `claude` is logged
into. Verify it works:

```
claude -p "Reply with the word OK" --output-format json
```

You should see a JSON result containing `"result":"OK"`. If `claude` isn't on
your PATH, note its full path and set `CLAUDE_CLI_PATH` in your `.env` (Step 6).

Usage counts against your Claude Code plan rather than a per-token bill. Each
summary loads a small fixed amount of context (~8k tokens on Haiku) and takes a
few seconds; the digest summarizes the top 10 articles per category.

---

## Step 5 — Create a Gmail App Password

1. Go to your Google Account → **Security**
2. Make sure **2-Step Verification** is ON
3. Go to **Security → App passwords**
4. Select app: **Mail**, device: **Windows Computer**
5. Click **Generate**
6. Copy the 16-character password (spaces don't matter)

---

## Step 6 — Create your `.env` file

Copy the example and fill in your values:

```
copy .env.example .env
```

Edit `.env`:

```
GMAIL_ADDRESS=jamie.sobczyk@gmail.com
GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
DIGEST_TO_EMAIL=jamie.sobczyk@gmail.com
GITHUB_PAGES_BASE_URL=https://YOUR-USERNAME.github.io/rss-digest
OPML_PATH=opml/feedly.opml
STATE_FILE=seen_articles.json
```

If `claude` is not on your PATH, also add:

```
CLAUDE_CLI_PATH=C:\Users\jamie\.local\bin\claude.exe
```

---

## Step 7 — Test with a dry run

This generates the HTML without pushing to GitHub or sending email:

```
python digest.py --dry-run
```

Open `docs\YYYY-MM-DD.html` in your browser to verify the output.

---

## Step 8 — Run for real

```
python digest.py
```

Check your inbox and visit your GitHub Pages URL.

---

## Step 9 — Schedule daily at 6:00 AM

Run once as Administrator (right-click PowerShell → "Run as administrator"):

```
python setup_task.py
```

This registers a Windows Task Scheduler task called `RssDigest` that runs every morning at 6:00 AM.

To verify it was created:
```
schtasks /Query /TN RssDigest
```

To remove it:
```
schtasks /Delete /TN RssDigest /F
```

---

## Troubleshooting

**No articles appear** — Feeds may not have published in the past 48 hours, or your OPML path is wrong. Check `OPML_PATH` in `.env`.

**Git push fails** — Make sure you've run `gh auth login` or configured SSH. The script uses whatever git credentials are active in your shell.

**Summaries are all just the article title or truncated text** — The `claude`
CLI couldn't be reached or isn't logged in. Run the Step 4 test command. If it
works in your terminal but fails under Task Scheduler, the scheduled task must
run as your own user account (not SYSTEM) so it can use your Claude Code login —
in `setup_task.py` the task is created for the current user, so register it while
logged in as yourself.

**Email not sending** — Double-check `GMAIL_APP_PASSWORD` (use the app-specific password, not your Gmail login password). Make sure 2FA is enabled on your Google account.

**Date formatting errors** — The script uses Windows-native `%#d` strftime format. If you run it on Linux/Mac, replace `%#d` with `%-d` and `%#I` with `%-I` in `mailer.py`, `renderer.py`, `digest.py`, and the templates.
