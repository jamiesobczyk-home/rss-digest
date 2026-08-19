#!/usr/bin/env python3
"""
rss-digest — daily RSS digest generator.

Usage:
    python digest.py              # full run
    python digest.py --dry-run    # generate HTML only; skip git push and email
"""

import argparse
import json
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
from dtfmt import pfmt


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


def main(dry_run: bool = False, no_email: bool = False) -> None:
    _force_utf8_output()

    repo_dir = Path(__file__).resolve().parent
    _start_logging(repo_dir)
    # Load .env explicitly from the script dir so a foreign CWD (e.g. Task
    # Scheduler's System32) can't change which file is loaded.
    load_dotenv(repo_dir / ".env")

    # Mail credentials are only required when this run will actually send.
    gmail_address = os.environ.get("GMAIL_ADDRESS")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD")
    to_email = os.environ.get("DIGEST_TO_EMAIL")
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

    # Clear any payload left by a previous run BEFORE anything can exit early.
    # The file is gitignored but survives in a reused container, so without
    # this a run that bails out (no new articles, fetch failure) leaves
    # yesterday's payload sitting there, perfectly readable — and the caller
    # emails yesterday's digest under today's date.
    payload_path = os.path.join(repo_dir, "email_payload.json")
    if os.path.exists(payload_path):
        os.remove(payload_path)
        print("[rss-digest] Cleared stale email_payload.json from a previous run")

    # Load state
    s = state.load(state_path)
    seen_ids = set(s["seen"].keys())

    # Feed source: OPML if present, else the committed feeds.yaml. The OPML
    # export is gitignored, so a fresh clone (any cloud run) has only the YAML.
    feeds_yaml = os.environ.get("FEEDS_FILE", "feeds.yaml")
    if not os.path.isabs(feeds_yaml):
        feeds_yaml = os.path.join(repo_dir, feeds_yaml)

    if os.path.exists(opml_path):
        print("[rss-digest] Parsing OPML...")
        categories = fetcher.parse_opml(opml_path)
    elif os.path.exists(feeds_yaml):
        print(f"[rss-digest] No OPML at {opml_path}; using {os.path.basename(feeds_yaml)}")
        categories = fetcher.parse_feeds_yaml(feeds_yaml)
    else:
        print(f"ERROR: no feed list. Looked for OPML at {opml_path} and {feeds_yaml}")
        sys.exit(1)

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
        # A quiet day and a dead scheduler must not look the same. Record the
        # run so the commit history is a complete delivery ledger: no
        # heartbeat for a date means the pipeline genuinely did not run.
        print(
            f"[rss-digest] No new articles across {total_feeds} feeds — "
            "no digest page today."
        )
        if dry_run:
            print("[dry-run] Skipping heartbeat commit.")
            return
        s["last_run"] = now.isoformat()
        s = state.prune(s)
        state.save(state_path, s)
        try:
            publisher.push(
                repo_dir,
                date_str,
                message=f"heartbeat: {date_str} (no new articles)",
            )
            print("[rss-digest] Heartbeat committed — pipeline ran, nothing new to publish.")
        except RuntimeError as exc:
            print(f"[rss-digest] WARNING: heartbeat commit failed: {exc}")
        print("[rss-digest] Done.")
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
        print(f"  Subject: {pfmt(now, 'Daily Digest — %a %b %#d')}")
        for a in preview:
            print(f"  - [{a['category']}] {a['title']}")
        print("\n[dry-run] State not updated — articles remain unseen so you can re-run.")
        print("[rss-digest] Done.")
        return
    else:
        # Update state BEFORE publishing: the publisher stages
        # seen_articles.json alongside docs/, and a cloud run starts from a
        # fresh clone, so state that is not committed is state that is lost.
        all_ids = [a["id"] for articles in categorized.values() for a in articles]
        s = state.mark_seen(s, all_ids, date_str)
        s = state.prune(s)
        s["last_run"] = now.isoformat()
        state.save(state_path, s)
        print(f"[rss-digest] State updated ({len(all_ids)} articles marked seen)")

        # Publish to GitHub Pages
        print("[rss-digest] Pushing to GitHub...")
        publisher.push(repo_dir, date_str)
        print("[rss-digest] Pushed")

        # Send email, unless the caller is sending it themselves.
        if no_email:
            payload = {
                "subject": pfmt(now, "Daily Digest — %a %b %#d"),
                "page_url": page_url,
                "date": date_str,
                "generated_at": now.isoformat(),
                "articles": [
                    {"category": a["category"], "title": a["title"], "link": a.get("link", "")}
                    for a in preview
                ],
            }
            with open(payload_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
            print(f"[rss-digest] --no-email: wrote {payload_path} for the caller to send")
        else:
            missing = [k for k, v in (("GMAIL_ADDRESS", gmail_address),
                                      ("GMAIL_APP_PASSWORD", gmail_password),
                                      ("DIGEST_TO_EMAIL", to_email)) if not v]
            if missing:
                print(f"ERROR: cannot send email, missing: {', '.join(missing)}")
                sys.exit(1)
            print("[rss-digest] Sending email...")
            mailer.send(now, page_url, preview, gmail_address, gmail_password, to_email)
            print("[rss-digest] Email sent")

    print("[rss-digest] Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate daily RSS digest")
    parser.add_argument("--dry-run", action="store_true", help="Skip push and email")
    parser.add_argument("--no-email", action="store_true",
                        help="Push and update state, but write email_payload.json instead of sending")
    args = parser.parse_args()
    main(dry_run=args.dry_run, no_email=args.no_email)
