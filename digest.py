#!/usr/bin/env python3
"""
rss-digest — daily RSS digest generator.

Usage:
    python digest.py              # full run
    python digest.py --dry-run    # generate HTML only; skip git push and email
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

import fetcher
import mailer
import publisher
import renderer
import state
import summarizer


def _force_utf8_output() -> None:
    """Ensure stdout/stderr can encode non-ASCII log chars (—, →, …).

    Under Task Scheduler stdout is a redirected pipe that defaults to the
    locale codepage (cp1252 on US Windows), so printing those characters
    raises UnicodeEncodeError and kills the run. Reconfigure to UTF-8.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


class _Tee:
    """Write to several streams at once (console + log file)."""

    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for s in self._streams:
            s.write(data)
            s.flush()

    def flush(self):
        for s in self._streams:
            s.flush()


def _start_logging(repo_dir: Path):
    """Tee stdout/stderr to logs/digest.log so scheduled runs leave a trace."""
    log_dir = repo_dir / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = open(log_dir / "digest.log", "a", encoding="utf-8", errors="replace")
    log_file.write(f"\n===== run started {datetime.now().astimezone():%Y-%m-%d %H:%M:%S %z} =====\n")
    sys.stdout = _Tee(sys.stdout, log_file)
    sys.stderr = _Tee(sys.stderr, log_file)
    return log_file


def main(dry_run: bool = False) -> None:
    _force_utf8_output()

    repo_dir = Path(__file__).resolve().parent
    _start_logging(repo_dir)
    # Load .env explicitly from the script dir so a foreign CWD (e.g. Task
    # Scheduler's System32) can't change which file is loaded.
    load_dotenv(repo_dir / ".env")

    gmail_address = os.environ["GMAIL_ADDRESS"]
    gmail_password = os.environ["GMAIL_APP_PASSWORD"]
    to_email = os.environ["DIGEST_TO_EMAIL"]
    base_url = os.environ["GITHUB_PAGES_BASE_URL"].rstrip("/")
    opml_path = os.environ.get("OPML_PATH", "opml/feedly.opml")
    state_path = os.environ.get("STATE_FILE", "seen_articles.json")
    # Feedly sections to leave out of the digest (comma-separated, case-insensitive).
    exclude_categories = {
        c.strip().lower()
        for c in os.environ.get("EXCLUDE_CATEGORIES", "Comics,Photography").split(",")
        if c.strip()
    }

    # Resolve relative config paths against the script dir, not the current
    # working directory, so scheduled runs (CWD = System32) still find them.
    if not os.path.isabs(opml_path):
        opml_path = str(repo_dir / opml_path)
    if not os.path.isabs(state_path):
        state_path = str(repo_dir / state_path)

    repo_dir = str(repo_dir)
    docs_dir = os.path.join(repo_dir, "docs")
    templates_dir = os.path.join(repo_dir, "templates")

    # Local time, so the digest's date/filename reflect the reader's day
    # rather than UTC (which rolls over mid-evening in the Americas).
    now = datetime.now().astimezone()
    date_str = now.strftime("%Y-%m-%d")

    print(f"[rss-digest] {date_str} — starting")

    # Load state
    s = state.load(state_path)
    seen_ids = set(s["seen"].keys())

    # Parse OPML
    if not os.path.exists(opml_path):
        print(f"ERROR: OPML file not found at {opml_path}")
        print("Export your Feedly feeds: Profile → Organize → Export OPML")
        sys.exit(1)

    print("[rss-digest] Parsing OPML...")
    categories = fetcher.parse_opml(opml_path)

    # Drop excluded sections before fetching (saves the network work too).
    if exclude_categories:
        dropped = [c for c in categories if c.lower() in exclude_categories]
        for c in dropped:
            del categories[c]
        if dropped:
            print(f"[rss-digest] Excluding sections: {', '.join(sorted(dropped))}")

    total_feeds = sum(len(v) for v in categories.values())
    print(f"[rss-digest] Found {len(categories)} categories, {total_feeds} feeds")

    # Fetch articles
    print("[rss-digest] Fetching feeds...")
    categorized = fetcher.fetch_all(categories, seen_ids)
    total_new = sum(len(v) for v in categorized.values())
    print(f"[rss-digest] {total_new} new articles across {len(categorized)} categories")

    if total_new == 0:
        print("[rss-digest] No new articles — skipping digest.")
        return

    # Summarize top-10 per category
    articles_to_summarize = [
        a for articles in categorized.values() for a in articles[:10]
    ]
    print(f"[rss-digest] Summarizing {len(articles_to_summarize)} articles...")
    summarizer.summarize(articles_to_summarize)
    print("[rss-digest] Summaries complete")

    # Build sections
    sections = renderer.build_sections(categorized)

    # Render daily page
    daily_path = renderer.render_daily(sections, now, docs_dir, templates_dir, base_url)
    print(f"[rss-digest] Wrote {daily_path}")

    # Regenerate index
    index_path = renderer.render_index(docs_dir, templates_dir, base_url)
    print(f"[rss-digest] Wrote {index_path}")

    # Page URL for this digest
    page_url = f"{base_url}/{date_str}.html"

    # Preview articles for email — 5 highlights, omitting the Misc section
    # (Misc still appears on the online digest page, just not in the email).
    preview = renderer.pick_preview_articles(sections, n=5, exclude={"Misc"})

    if dry_run:
        print("\n[dry-run] Skipping git push and email send.")
        print(f"[dry-run] Digest page: {daily_path}")
        print(f"[dry-run] Page URL would be: {page_url}")
        print("\n[dry-run] Email preview:")
        print(f"  Subject: {now.strftime('Daily Digest — %a %b %#d')}")
        for a in preview:
            print(f"  - [{a['category']}] {a['title']}")
        print("\n[dry-run] State not updated — articles remain unseen so you can re-run.")
        print("[rss-digest] Done.")
        return
    else:
        # Publish to GitHub Pages
        print("[rss-digest] Pushing to GitHub...")
        publisher.push(repo_dir, date_str)
        print("[rss-digest] Pushed")

        # Send email
        print("[rss-digest] Sending email...")
        mailer.send(now, page_url, preview, gmail_address, gmail_password, to_email)
        print("[rss-digest] Email sent")

    # Update state — mark all processed articles as seen
    all_ids = [a["id"] for articles in categorized.values() for a in articles]
    s = state.mark_seen(s, all_ids, date_str)
    s = state.prune(s)
    s["last_run"] = now.isoformat()
    state.save(state_path, s)
    print(f"[rss-digest] State updated ({len(all_ids)} articles marked seen)")
    print("[rss-digest] Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate daily RSS digest")
    parser.add_argument("--dry-run", action="store_true", help="Skip push and email")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
