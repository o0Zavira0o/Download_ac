#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup

# ------------------------------
# تنظیمات
# ------------------------------
REPO_DIR = "articles"
LOG_FILE = "log.txt"
HISTORY_FILE = "processed_urls.txt"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
TIMEOUT = 30
MAX_AGE_HOURS = 48  # فقط ۴۸ ساعت اخیر

SOURCES = [
    {
        "name": "Foreign_Affairs",
        "rss": "https://www.foreignaffairs.com/rss.xml",
        "base_url": "https://www.foreignaffairs.com",
    },
    {
        "name": "Foreign_Policy",
        "rss": "https://foreignpolicy.com/feed/",
        "base_url": "https://foreignpolicy.com",
    },
    {
        "name": "New_Yorker_Magazine",
        "rss": "https://www.newyorker.com/feed/everything",
        "base_url": "https://www.newyorker.com",
        "url_filter": "/magazine/",
    },
]

# ------------------------------
# لاگ
# ------------------------------
def reset_log() -> None:
    """در ابتدای هر اجرا، فایل لاگ را خالی می‌کند."""
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write("")


def write_log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ------------------------------
# تاریخچهٔ URLها
# ------------------------------
def ensure_history_file() -> None:
    if not os.path.exists(HISTORY_FILE):
        open(HISTORY_FILE, "a", encoding="utf-8").close()


def load_history() -> set:
    if not os.path.exists(HISTORY_FILE):
        return set()
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def save_history(new_urls: set) -> set:
    history = load_history()
    history.update(new_urls)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        for url in sorted(history):
            f.write(url + "\n")
    return history


# ------------------------------
# کمک برای RSS
# ------------------------------
def parse_date_rfc2822(date_str: str):
    from email.utils import parsedate_to_datetime

    try:
        dt = parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def fetch_from_rss(rss_url: str, source_name: str, url_filter: str | None = None) -> list:
    """
    خروجی هر آیتم:
    {
      "source": source_name,
      "title": "...",
      "original_url": "...",
      "pub_date": "<ISO8601>",
    }
    """
    articles = []
    write_log(f"Fetching RSS: {rss_url}")

    try:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        }
        r = requests.get(rss_url, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()

        feed = feedparser.parse(r.text)
        if feed.bozo and not feed.entries:
            write_log(f"RSS bozo for {source_name}: {feed.bozo_exception}")

        cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)

        for entry in feed.entries:
            link = entry.get("link")
            if not link:
                continue
            if url_filter and url_filter not in link:
                continue

            pub_date = None
            if getattr(entry, "published_parsed", None):
                pub_date = datetime(
                    *entry.published_parsed[:6], tzinfo=timezone.utc
                )
            elif getattr(entry, "updated_parsed", None):
                pub_date = datetime(
                    *entry.updated_parsed[:6], tzinfo=timezone.utc
                )
            elif getattr(entry, "published", None):
                pub_date = parse_date_rfc2822(entry.published)

            if not pub_date:
                continue

            if pub_date >= cutoff:
                title = entry.get("title", "No Title").strip()
                articles.append(
                    {
                        "source": source_name,
                        "title": title,
                        "original_url": link,
                        "pub_date": pub_date.isoformat(),
                    }
                )

        write_log(f"RSS {source_name}: {len(articles)} recent articles.")
    except Exception as e:
        write_log(f"RSS error [{source_name}]: {e}")

    return articles


# ------------------------------
# اسکرپ HTML برای نیویورکر مجله
# ------------------------------
def scrape_newyorker_magazine_html() -> list:
    url = "https://www.newyorker.com/magazine"
    articles = []
    write_log("Scraping New Yorker magazine HTML...")

    try:
        headers = {"User-Agent": USER_AGENT}
        r = requests.get(url, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)

        for a in soup.select('a[data-link-type="article"], a[href*="/magazine/"]'):
            href = a.get("href")
            if not href:
                continue
            full_url = urljoin("https://www.newyorker.com", href)
            if "/magazine/" not in full_url:
                continue

            m = re.search(r"/magazine/(\d{4})/(\d{2})/(\d{2})/", full_url)
            if not m:
                continue

            y, mth, d = map(int, m.groups())
            pub_date = datetime(y, mth, d, tzinfo=timezone.utc)
            if pub_date < cutoff:
                continue

            title = a.get_text(strip=True) or "Magazine Article"

            articles.append(
                {
                    "source": "New_Yorker_Magazine",
                    "title": title,
                    "original_url": full_url,
                    "pub_date": pub_date.isoformat(),
                }
            )

        uniq = []
        seen = set()
        for art in articles:
            if art["original_url"] in seen:
                continue
            seen.add(art["original_url"])
            uniq.append(art)

        write_log(f"Scraped {len(uniq)} New Yorker magazine articles from HTML.")
        return uniq

    except Exception as e:
        write_log(f"New Yorker HTML scrape error: {e}")
        return []


# ------------------------------
# Wayback Machine (web.archive.org)
# ------------------------------
def get_wayback_latest_snapshot(original_url: str) -> str | None:
    """
    ۱) با API /wayback/available آخرین اسنپ‌شات موجود را می‌گیرد.
    ۲) اگر نبود، None برمی‌گرداند (ساخت اسنپ‌شات جدید را تابع دیگر انجام می‌دهد).
    """
    api_url = "https://archive.org/wayback/available"
    headers = {"User-Agent": USER_AGENT}
    try:
        r = requests.get(
            api_url,
            params={"url": original_url},
            headers=headers,
            timeout=TIMEOUT,
        )
        if not (200 <= r.status_code < 300):
            write_log(f"Wayback available API status {r.status_code} for {original_url}")
            return None

        data = r.json()
        closest = data.get("archived_snapshots", {}).get("closest")
        if closest and closest.get("available") and closest.get("status") == "200":
            snap_url = closest.get("url")
            if snap_url:
                write_log(f"Wayback existing snapshot: {snap_url}")
                return snap_url
        return None
    except Exception as e:
        write_log(f"Wayback available API error for {original_url}: {e}")
        return None


def create_wayback_snapshot(original_url: str) -> str | None:
    """
    تلاش برای ساخت اسنپ‌شات جدید در Wayback:
      GET https://web.archive.org/save/<url>
    و برداشتن مسیر از هدر Content-Location.
    """
    save_url = f"https://web.archive.org/save/{original_url}"
    headers = {"User-Agent": USER_AGENT}
    try:
        r = requests.get(save_url, headers=headers, timeout=TIMEOUT)
        # Wayback معمولاً با 200/302 جواب می‌دهد و Content-Location می‌گذارد
        cl = r.headers.get("Content-Location")
        if cl:
            if not cl.startswith("http"):
                snap_url = "https://web.archive.org" + cl
            else:
                snap_url = cl
            write_log(f"Wayback new snapshot created: {snap_url}")
            return snap_url
        else:
            write_log(f"Wayback save: no Content-Location for {original_url}")
            return None
    except Exception as e:
        write_log(f"Wayback save error for {original_url}: {e}")
        return None


def get_archive_snapshot_url(original_url: str) -> str | None:
    """
    تابع اصلی برای گرفتن URL اسنپ‌شات:
      - ابتدا سعی می‌کند آخرین اسنپ‌شات موجود در Wayback را بگیرد.
      - اگر نبود، یک‌بار تلاش می‌کند اسنپ‌شات جدید بسازد.
    """
    # فاز ۱: آخرین اسنپ‌شات موجود
    snap = get_wayback_latest_snapshot(original_url)
    if snap:
        return snap

    # فاز ۲: ساخت اسنپ‌شات جدید
    time.sleep(2)  # کمی مکث بین available و save
    snap = create_wayback_snapshot(original_url)
    return snap


# ------------------------------
# خواندن فایل خروجی موجود
# ------------------------------
def load_existing_output() -> list:
    """
    مقاله‌های معتبر باقی‌مانده از فایل قبلی را برمی‌گرداند.
    ساختار هر ورودی:
    {source, title, original_url, archive_url, pub_date}
    """
    out_path = os.path.join(REPO_DIR, "latest_archives.txt")
    if not os.path.exists(out_path):
        return []

    entries = []
    try:
        with open(out_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("[") and "]" in line:
                entry = {}
                header = line
                entry["source"] = header.split("]")[0][1:]
                entry["title"] = header.split("]", 1)[1].strip()

                if i + 1 < len(lines) and lines[i + 1].startswith("Published:"):
                    entry["pub_date"] = (
                        lines[i + 1].replace("Published:", "").strip()
                    )
                    i += 1

                if i + 1 < len(lines) and lines[i + 1].startswith("Original:"):
                    entry["original_url"] = (
                        lines[i + 1].replace("Original:", "").strip()
                    )
                    i += 1

                if i + 1 < len(lines) and lines[i + 1].startswith("Archive :"):
                    entry["archive_url"] = (
                        lines[i + 1].replace("Archive :", "").strip()
                    )
                    i += 1

                if i + 1 < len(lines) and lines[i + 1].startswith("---"):
                    i += 1

                if all(
                    k in entry
                    for k in (
                        "source",
                        "title",
                        "original_url",
                        "archive_url",
                        "pub_date",
                    )
                ):
                    entries.append(entry)
                else:
                    write_log(f"Skipping incomplete entry starting: {line}")

            i += 1

    except Exception as e:
        write_log(f"Error reading existing output: {e}")
        return []

    return entries


# ------------------------------
# main
# ------------------------------
def main():
    reset_log()
    write_log("=== Job Started (Wayback-based fetcher_v2) ===")

    try:
        os.makedirs(REPO_DIR, exist_ok=True)
        ensure_history_file()

        history = load_history()
        write_log(f"History loaded: {len(history)} URLs")

        # ۱) جمع‌آوری از RSS
        new_articles: list[dict] = []
        for src in SOURCES:
            rss_articles = fetch_from_rss(
                rss_url=src["rss"],
                source_name=src["name"],
                url_filter=src.get("url_filter"),
            )
            for art in rss_articles:
                if art["original_url"] not in history:
                    new_articles.append(art)

        # ۲) HTML برای New Yorker magazine
        ny_html_articles = scrape_newyorker_magazine_html()
        for art in ny_html_articles:
            if art["original_url"] not in history:
                new_articles.append(art)

        write_log(f"Total new articles to process: {len(new_articles)}")

        # ۳) آرشیو کردن در Wayback
        new_archive_entries = []
        new_urls_set = set()

        new_articles.sort(key=lambda a: a["pub_date"])

        for art in new_articles:
            write_log(f"Archiving (Wayback): {art['original_url']}")
            time.sleep(3)  # کمی تاخیر برای پرهیز از فشار زیاد
            arch_url = get_archive_snapshot_url(art["original_url"])
            if arch_url:
                new_archive_entries.append(
                    {
                        "source": art["source"],
                        "title": art["title"],
                        "original_url": art["original_url"],
                        "archive_url": arch_url,
                        "pub_date": art["pub_date"],
                    }
                )
                new_urls_set.add(art["original_url"])
            else:
                write_log(f"Failed to archive (Wayback): {art['original_url']}")

        if new_urls_set:
            save_history(new_urls_set)
            write_log(f"History updated, +{len(new_urls_set)} URLs")

        # ۴) ترکیب با خروجی قبلی و حذف قدیمی‌تر از ۴۸ ساعت
        cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
        existing_entries = load_existing_output()
        final_entries = []
        kept_urls = set()

        for e in new_archive_entries:
            final_entries.append(e)
            kept_urls.add(e["original_url"])

        for e in existing_entries:
            if e["original_url"] in kept_urls:
                continue
            try:
                pub_dt = datetime.fromisoformat(e["pub_date"])
                if pub_dt >= cutoff:
                    final_entries.append(e)
                    kept_urls.add(e["original_url"])
            except Exception:
                continue

        final_entries.sort(key=lambda e: e["pub_date"], reverse=True)

        # ۵) نوشتن خروجی
        out_path = os.path.join(REPO_DIR, "latest_archives.txt")
        now_utc = datetime.now(timezone.utc)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(
                f"# Last Run: {now_utc.strftime('%Y-%m-%d %H:%M')} UTC\n"
            )
            f.write(
                f"# Articles published in last {MAX_AGE_HOURS} hours\n\n"
            )

            if final_entries:
                for e in final_entries:
                    f.write(f"[{e['source']}] {e['title']}\n")
                    f.write(f"Published: {e['pub_date']}\n")
                    f.write(f"Original: {e['original_url']}\n")
                    f.write(f"Archive : {e['archive_url']}\n")
                    f.write("---\n")
            else:
                f.write("No articles archived in this run.\n")

        write_log(f"Output written with {len(final_entries)} entries.")
    except Exception as e:
        write_log(f"FATAL ERROR: {e}")
        raise
    finally:
        write_log("=== Job Finished ===")


if __name__ == "__main__":
    main()
