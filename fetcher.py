import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import feedparser


def parse_opml(opml_path: str) -> dict[str, list[str]]:
    """Return {category: [xmlUrl, ...]}. Uncategorized feeds go under 'Uncategorized'."""
    tree = ET.parse(opml_path)
    body = tree.getroot().find("body")
    categories: dict[str, list[str]] = {}

    for outline in body:
        feed_url = outline.get("xmlUrl")
        if feed_url:
            # Top-level feed with no category folder
            categories.setdefault("Uncategorized", []).append(feed_url)
        else:
            # Category folder — collect child feeds
            cat_name = outline.get("title") or outline.get("text") or "Uncategorized"
            urls = [
                child.get("xmlUrl")
                for child in outline
                if child.get("xmlUrl")
            ]
            if urls:
                categories.setdefault(cat_name, []).extend(urls)

    return categories


def _article_id(entry) -> str:
    return entry.get("id") or entry.get("link") or entry.get("title", "")


def _parse_date(entry) -> datetime:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        return datetime(*parsed[:6], tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _fetch_feed(feed_url: str, category: str, seen: set, cutoff: datetime) -> list[dict]:
    try:
        feed = feedparser.parse(feed_url, request_headers={"User-Agent": "rss-digest/1.0"})
    except Exception:
        return []

    source_title = feed.feed.get("title") or urlparse(feed_url).netloc
    articles = []

    for entry in feed.entries:
        pub = _parse_date(entry)
        if pub < cutoff:
            continue
        aid = _article_id(entry)
        if aid in seen:
            continue

        raw_summary = (
            entry.get("content", [{}])[0].get("value")
            or entry.get("summary")
            or ""
        )

        articles.append({
            "id": aid,
            "title": entry.get("title", "(no title)").strip(),
            "link": entry.get("link", ""),
            "raw_summary": raw_summary,
            "published": pub,
            "source": source_title,
            "category": category,
            "summary": None,  # filled in by summarizer
        })

    return articles


def fetch_all(
    categories: dict[str, list[str]],
    seen_ids: set,
    hours_back: int = 48,
    max_workers: int = 20,
) -> dict[str, list[dict]]:
    """Fetch all feeds in parallel. Returns {category: [article, ...]} sorted newest-first."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    jobs = [
        (feed_url, cat)
        for cat, urls in categories.items()
        for feed_url in urls
    ]

    results: dict[str, list[dict]] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_fetch_feed, url, cat, seen_ids, cutoff): (url, cat)
            for url, cat in jobs
        }
        for future in as_completed(futures):
            articles = future.result()
            for article in articles:
                cat = article["category"]
                results.setdefault(cat, []).append(article)

    # Sort each category newest-first
    for cat in results:
        results[cat].sort(key=lambda a: a["published"], reverse=True)

    return results
