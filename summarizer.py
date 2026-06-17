import asyncio
import json
import os
import re
import shutil
import subprocess
import warnings

import requests
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning

# Feed "summaries" are sometimes just a bare URL; we still hand them to the HTML
# stripper, which warns. The warning is spurious for our use, so silence it.
warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)

MIN_SUMMARY_CHARS = 200
MAX_FETCH_CHARS = 4000
REQUEST_TIMEOUT = 8
# claude CLI invocations are heavier than direct API calls (each spawns a
# process and reloads context), so keep concurrency modest.
CONCURRENCY = int(os.environ.get("SUMMARIZER_CONCURRENCY", "4"))
CLI_TIMEOUT = int(os.environ.get("SUMMARIZER_CLI_TIMEOUT", "90"))
MODEL = os.environ.get("SUMMARIZER_MODEL", "claude-haiku-4-5-20251001")

SYSTEM_PROMPT = (
    "You are a concise news summarizer. Summarize the provided article in 2-3 sentences "
    "for a busy reader, using ONLY the text given. Be direct and factual. "
    "Never ask for more content, never say the text is truncated or incomplete, and never "
    "address the reader — output only the summary itself. If only a little text is "
    "available, summarize what is present in a single sentence. Do not editorialize or use "
    "filler phrases like 'This article discusses' or 'The author explains'."
)

# Tools and MCP servers are disabled per call: this is pure text summarization,
# and dropping them roughly halves the context the CLI loads on each spawn.
_DISABLED_TOOLS = [
    "Bash", "Read", "Edit", "Write", "Glob", "Grep",
    "WebFetch", "WebSearch", "Task", "TodoWrite", "NotebookEdit",
]


def _claude_cli() -> str:
    """Locate the Claude Code CLI. Override with CLAUDE_CLI_PATH in .env."""
    override = os.environ.get("CLAUDE_CLI_PATH")
    if override and os.path.exists(override):
        return override
    found = shutil.which("claude")
    if found:
        return found
    default = os.path.expanduser(os.path.join("~", ".local", "bin", "claude.exe"))
    if os.path.exists(default):
        return default
    raise RuntimeError(
        "Could not find the `claude` CLI. Install Claude Code or set "
        "CLAUDE_CLI_PATH in your .env to the full path of claude(.exe)."
    )


def _strip_html(raw: str) -> str:
    return BeautifulSoup(raw, "lxml").get_text(separator=" ", strip=True)


def _fetch_article_text(url: str) -> str:
    try:
        resp = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "rss-digest/1.0"},
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        # Remove nav, footer, ads
        for tag in soup(["nav", "footer", "aside", "script", "style", "header"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
        # Collapse whitespace
        text = re.sub(r"\s+", " ", text)
        return text[:MAX_FETCH_CHARS]
    except Exception:
        return ""


def _summarize_with_cli(cli: str, title: str, body: str) -> str | None:
    """Summarize one article via the headless Claude Code CLI.

    Uses the caller's existing Claude Code login (no API key). Returns the
    summary text, or None on any failure so the caller can fall back.
    """
    prompt = f"Title: {title}\n\n{body}"
    cmd = [
        cli,
        "-p", prompt,
        "--model", MODEL,
        "--system-prompt", SYSTEM_PROMPT,
        "--output-format", "json",
        "--exclude-dynamic-system-prompt-sections",
        "--strict-mcp-config",
        "--disallowed-tools", *_DISABLED_TOOLS,
    ]
    try:
        result = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,  # avoid the CLI waiting on piped stdin
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=CLI_TIMEOUT,
        )
    except Exception:
        return None

    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return None
    if data.get("is_error"):
        return None
    summary = (data.get("result") or "").strip()
    return summary or None


async def _summarize_one(
    cli: str,
    article: dict,
    semaphore: asyncio.Semaphore,
) -> dict:
    async with semaphore:
        # Prefer feed-provided content; fall back to fetching the page
        raw = _strip_html(article["raw_summary"])
        if len(raw) < MIN_SUMMARY_CHARS:
            fetched = await asyncio.to_thread(_fetch_article_text, article["link"])
            if fetched:
                raw = fetched

        if not raw:
            article["summary"] = article["title"]
            return article

        summary = await asyncio.to_thread(_summarize_with_cli, cli, article["title"], raw)
        if summary:
            article["summary"] = summary
        else:
            # Fallback to truncated raw text
            article["summary"] = raw[:300] + ("…" if len(raw) > 300 else "")

        return article


async def _summarize_all_async(articles: list[dict]) -> list[dict]:
    cli = _claude_cli()
    semaphore = asyncio.Semaphore(CONCURRENCY)
    tasks = [_summarize_one(cli, a, semaphore) for a in articles]
    return await asyncio.gather(*tasks)


def summarize(articles: list[dict]) -> list[dict]:
    """Summarize a flat list of articles in-place (concurrent). Returns the same list.

    Intelligence runs through the local Claude Code CLI in headless mode, using
    your existing Claude Code login rather than an Anthropic API key.
    """
    return asyncio.run(_summarize_all_async(articles))
