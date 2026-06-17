import os
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader


def _env(templates_dir: str) -> Environment:
    env = Environment(
        loader=FileSystemLoader(templates_dir),
        autoescape=True,
    )
    # Convert tz-aware (UTC) article timestamps to the local timezone for display.
    env.filters["localtime"] = lambda dt: dt.astimezone() if dt else dt
    return env


def _split_articles(articles: list[dict], top_n: int = 10) -> tuple[list, list]:
    return articles[:top_n], articles[top_n:]


def build_sections(categorized: dict[str, list[dict]], top_n: int = 10) -> list[dict]:
    """Convert {category: [articles]} into a list of section dicts for the template."""
    sections = []
    for category, articles in sorted(categorized.items()):
        top, overflow = _split_articles(articles, top_n)
        sections.append({
            "name": category,
            "top": top,
            "overflow": overflow,
            "total": len(articles),
        })
    return sections


def render_daily(
    sections: list[dict],
    date: datetime,
    docs_dir: str,
    templates_dir: str,
    base_url: str,
) -> str:
    """Render the daily digest HTML page. Returns the output file path."""
    env = _env(templates_dir)
    tmpl = env.get_template("digest.html.j2")
    date_str = date.strftime("%Y-%m-%d")
    html = tmpl.render(
        sections=sections,
        date=date,
        date_str=date_str,
        base_url=base_url.rstrip("/"),
        total_articles=sum(s["total"] for s in sections),
    )
    out_path = os.path.join(docs_dir, f"{date_str}.html")
    Path(out_path).write_text(html, encoding="utf-8")
    return out_path


def render_index(
    docs_dir: str,
    templates_dir: str,
    base_url: str,
) -> str:
    """Regenerate the archive index page from all dated HTML files in docs/."""
    env = _env(templates_dir)
    tmpl = env.get_template("index.html.j2")

    dated_files = sorted(
        [f for f in os.listdir(docs_dir) if f.endswith(".html") and f != "index.html"],
        reverse=True,
    )
    entries = []
    for fname in dated_files:
        date_str = fname.replace(".html", "")
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            entries.append({"date_str": date_str, "label": dt.strftime("%A, %B %#d, %Y"), "file": fname})
        except ValueError:
            pass

    html = tmpl.render(entries=entries, base_url=base_url.rstrip("/"))
    out_path = os.path.join(docs_dir, "index.html")
    Path(out_path).write_text(html, encoding="utf-8")
    return out_path


def pick_preview_articles(sections: list[dict], n: int = 3) -> list[dict]:
    """Pick the first n summarized articles across all sections for the email preview."""
    preview = []
    for section in sections:
        for article in section["top"]:
            preview.append({**article, "category": section["name"]})
            if len(preview) >= n:
                return preview
    return preview
