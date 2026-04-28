#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, quote_plus, urlparse

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
TIMEOUT = 30                # برای هر request
MAX_AGE_HOURS = 48          # فقط ۴۸ ساعت گذشته
SLEEP_BETWEEN_ARTICLES = 12 # تاخیر بین هر مقاله
SLEEP_BETWEEN_DOMAINS = 3   # تاخیر کوتاه بین دامنه‌ها

# دامنه‌های آرشیو به ترتیب اولویت
ARCHIVE_DOMAINS = [
    "archive.is",
    "archive.ph",
    "archive.md",
]

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
# کمک برای تاریخ RSS
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


# ------------------------------
# جمع‌آوری از RSS
# ------------------------------
def fetch_from_rss(rss_url: str, source_name: str, url_filter: str | None = None) -> list:
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
# اسکرپ HTML برای New Yorker مجله
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
# تشخیص لینک اسنپ‌شات معتبر
# ------------------------------
def is_valid_snapshot_url(url: str) -> bool:
    """
    بررسی می‌کند آیا url یک اسنپ‌شات واقعی در یکی از دامنه‌های archive.* است.
    """
    parsed = urlparse(url)
    if not any(domain in parsed.netloc for domain in ARCHIVE_DOMAINS):
        return False

    # مسیرهایی که نشان‌دهندهٔ صفحهٔ submit/job/search هستند
    bad_path_starts = ("/submit", "/search", "/tag/", "/tags/", "/list/")
    bad_params = ("?q=", "&q=", "?run=", "&run=")

    if any(parsed.path.startswith(p) for p in bad_path_starts):
        return False
    if any(bp in url for bp in bad_params):
        return False

    # صفحهٔ ریشهٔ دامنه نباشد
    if parsed.path in ("", "/"):
        return False

    return True


# ------------------------------
# درخواست به یک دامنهٔ archive.*
# ------------------------------
def try_get_snapshot_from_domain(original_url: str, domain: str) -> str | None:
    """
    برای یک دامنهٔ مشخص:
      ۱. GET /submit/?url=... ← اگر اسنپ‌شات بالفعل وجود داشته باشد، redirect می‌کند.
      ۲. اگر نشد، POST /submit/ ← تلاش برای ساخت اسنپ‌شات جدید.
    در صورت بروز خطای شبکه یا status غیرعادی (429, 5xx) با شرح به لاگ برمی‌گردد
    و None می‌دهد.
    """
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": f"https://{domain}/",
    }
    base = f"https://{domain}"
    submit_url_get = f"{base}/submit/?url={quote_plus(original_url)}"
    submit_url_post = f"{base}/submit/"

    # ۱) تلاش GET
    try:
        write_log(f"  [{domain}] GET {submit_url_get}")
        resp = requests.get(
            submit_url_get,
            headers=headers,
            timeout=TIMEOUT,
            allow_redirects=True,
        )
        write_log(f"  [{domain}] GET returned status {resp.status_code}, final URL: {resp.url}")

        # هر وضعیتی که به یک اسنپ‌شات redirect کند، قبول است
        if 200 <= resp.status_code < 400:
            candidate = resp.url
            if is_valid_snapshot_url(candidate):
                write_log(f"  [{domain}] ✅ Found existing snapshot: {candidate}")
                return candidate
        elif resp.status_code == 429:
            write_log(f"  [{domain}] ⚠️ 429 Too Many Requests – rate limited")
            return None
        elif resp.status_code >= 500:
            write_log(f"  [{domain}] ⛔ Server error {resp.status_code}")
            return None
    except requests.exceptions.Timeout:
        write_log(f"  [{domain}] ❗ Timeout on GET")
        return None
    except Exception as e:
        write_log(f"  [{domain}] ❗ Exception on GET: {e}")
        return None

    # ۲) اگر هنوز نرسیده‌ایم، یک فرصت POST بدهیم
    # wait a bit before POST
    time.sleep(2)

    try:
        data = {"url": original_url, "anyway": "1"}
        write_log(f"  [{domain}] POST {submit_url_post}")
        resp = requests.post(
            submit_url_post,
            data=data,
            headers=headers,
            timeout=TIMEOUT,
            allow_redirects=True,
        )
        write_log(f"  [{domain}] POST returned status {resp.status_code}, final URL: {resp.url}")

        if 200 <= resp.status_code < 400:
            candidate = resp.url
            if is_valid_snapshot_url(candidate):
                write_log(f"  [{domain}] ✅ New snapshot created: {candidate}")
                return candidate
        elif resp.status_code == 429:
            write_log(f"  [{domain}] ⚠️ 429 Too Many Requests on POST")
        elif resp.status_code >= 500:
            write_log(f"  [{domain}] ⛔ Server error {resp.status_code} on POST")
    except requests.exceptions.Timeout:
        write_log(f"  [{domain}] ❗ Timeout on POST")
    except Exception as e:
        write_log(f"  [{domain}] ❗ Exception on POST: {e}")

    return None


def get_archive_snapshot(original_url: str) -> str | None:
    """
    به ترتیب روی archive.is → archive.ph → archive.md تلاش می‌کند.
    هر وقت یک لینک اسنپ‌شات معتبر پیدا شد، همان را برمی‌گرداند.
    در غیر این صورت None.
    """
    for domain in ARCHIVE_DOMAINS:
        snap = try_get_snapshot_from_domain(original_url, domain)
        if snap:
            return snap
        # اگر دامنه خطا داد یا ۴۲۹، کمی استراحت کنیم و برویم بعدی
        time.sleep(SLEEP_BETWEEN_DOMAINS)

    write_log(f"  ❌ No snapshot found on any archive domain.")
    return None


# ------------------------------
# خواندن خروجی قبلی
# ------------------------------
def load_existing_output() -> list:
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
                    entry["pub_date"] = lines[i + 1].replace("Published:", "").strip()
                    i += 1

                if i + 1 < len(lines) and lines[i + 1].startswith("Original:"):
                    entry["original_url"] = lines[i + 1].replace("Original:", "").strip()
                    i += 1

                if i + 1 < len(lines) and lines[i + 1].startswith("Archive :"):
                    entry["archive_url"] = lines[i + 1].replace("Archive :", "").strip()
                    i += 1

                if i + 1 < len(lines) and lines[i + 1].startswith("---"):
                    i += 1

                if all(
                    k in entry
                    for k in ("source", "title", "original_url", "archive_url", "pub_date")
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
    write_log("=== Job Started (archive.is/.ph/.md with careful rate limiting) ===")

    try:
        os.makedirs(REPO_DIR, exist_ok=True)
        ensure_history_file()

        history = load_history()
        write_log(f"History loaded: {len(history)} URLs")

        # ۱) جمع‌آوری مقالات جدید
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

        ny_html_articles = scrape_newyorker_magazine_html()
        for art in ny_html_articles:
            if art["original_url"] not in history:
                new_articles.append(art)

        write_log(f"Total new articles to process: {len(new_articles)}")

        # ۲) آرشیو کردن
        new_archive_entries = []
        new_urls_set = set()

        # مرتب‌سازی بر اساس تاریخ (اختیاری)
        new_articles.sort(key=lambda a: a["pub_date"])

        for idx, art in enumerate(new_articles, start=1):
            write_log(f"({idx}/{len(new_articles)}) Archiving: {art['original_url']}")
            snap = get_archive_snapshot(art["original_url"])
            if snap:
                new_archive_entries.append(
                    {
                        "source": art["source"],
                        "title": art["title"],
                        "original_url": art["original_url"],
                        "archive_url": snap,
                        "pub_date": art["pub_date"],
                    }
                )
                new_urls_set.add(art["original_url"])
                write_log(f"  ✔ Snapshot: {snap}")
            else:
                write_log(f"  ✖ Could not archive: {art['original_url']}")

            # تاخیر بین مقالات برای جلوگیری از ۴۲۹
            if idx < len(new_articles):
                time.sleep(SLEEP_BETWEEN_ARTICLES)

        # ذخیره‌سازی تاریخچه
        if new_urls_set:
            save_history(new_urls_set)
            write_log(f"History updated, +{len(new_urls_set)} URLs")

        # ۳) ترکیب با خروجی قبلی و حذف قدیمی‌ها
        cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
        existing = load_existing_output()
        final_entries = []
        kept_urls = set()

        # ابتدا تازه‌ها
        for e in new_archive_entries:
            final_entries.append(e)
            kept_urls.add(e["original_url"])

        # سپس قدیمی‌هایی که هنوز در بازه هستند و تکراری نیستند
        for e in existing:
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

        # ۴) نوشتن خروجی
        out_path = os.path.join(REPO_DIR, "latest_archives.txt")
        now_utc = datetime.now(timezone.utc)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"# Last Run: {now_utc.strftime('%Y-%m-%d %H:%M')} UTC\n")
            f.write(f"# Articles published in last {MAX_AGE_HOURS} hours\n\n")

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
