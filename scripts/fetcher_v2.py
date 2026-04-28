#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, quote_plus

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
TIMEOUT = 20
MAX_AGE_HOURS = 48

ARCHIVE_DOMAINS = [
    "archive.is",
    "archive.ph",
    "archive.md",
]

SOURCES = [
    {
        "name": "Foreign_Affairs",
        "rss": "https://www.foreignaffairs.com/rss.xml",
    },
    {
        "name": "Foreign_Policy",
        "rss": "https://foreignpolicy.com/feed/",
    },
    {
        "name": "New_Yorker_Magazine",
        "rss": "https://www.newyorker.com/feed/everything",
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


def save_history(new_urls: set) -> None:
    history = load_history()
    history.update(new_urls)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        for url in sorted(history):
            f.write(url + "\n")


# ------------------------------
# RSS
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
    articles = []
    write_log(f"Fetching RSS: {source_name}")

    try:
        headers = {"User-Agent": USER_AGENT}
        r = requests.get(rss_url, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()

        feed = feedparser.parse(r.text)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)

        for entry in feed.entries:
            link = entry.get("link")
            if not link:
                continue
            if url_filter and url_filter not in link:
                continue

            pub_date = None
            if getattr(entry, "published_parsed", None):
                pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            elif getattr(entry, "updated_parsed", None):
                pub_date = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
            elif getattr(entry, "published", None):
                pub_date = parse_date_rfc2822(entry.published)

            if not pub_date or pub_date < cutoff:
                continue

            title = entry.get("title", "No Title").strip()
            articles.append({
                "source": source_name,
                "title": title,
                "original_url": link,
                "pub_date": pub_date.isoformat(),
            })

        write_log(f"RSS {source_name}: {len(articles)} recent articles")
    except Exception as e:
        write_log(f"RSS error [{source_name}]: {e}")

    return articles


# ------------------------------
# New Yorker HTML
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
            articles.append({
                "source": "New_Yorker_Magazine",
                "title": title,
                "original_url": full_url,
                "pub_date": pub_date.isoformat(),
            })

        # حذف تکراری‌ها
        uniq = []
        seen = set()
        for art in articles:
            if art["original_url"] not in seen:
                seen.add(art["original_url"])
                uniq.append(art)

        write_log(f"New Yorker HTML: {len(uniq)} articles")
        return uniq

    except Exception as e:
        write_log(f"New Yorker HTML error: {e}")
        return []


# ------------------------------
# استخراج Snapshot از HTML
# ------------------------------
def extract_snapshot_url_from_html(html_content: str, domain: str) -> str | None:
    """
    تلاش برای پیدا کردن URL اسنپ‌شات واقعی از HTML برگردانده‌شده.
    
    الگوهای چک:
    1. meta refresh با url=...
    2. og:url یا canonical
    3. لینک‌های <a href> که روی archive.* باشند
    4. توکن‌های صریح مثل /20240101120000/ در لینک‌ها
    """
    soup = BeautifulSoup(html_content, "html.parser")
    
    candidates = []

    # ۱) meta refresh
    for meta in soup.find_all("meta"):
        content = meta.get("content", "")
        http_equiv = meta.get("http-equiv", "")
        if http_equiv.lower() == "refresh" and "url=" in content:
            m = re.search(r"url=(['\"]?)(.+?)\1", content, re.I)
            if m:
                url = m.group(2).strip()
                if url.startswith("//"):
                    url = "https:" + url
                elif url.startswith("/"):
                    url = f"https://{domain}{url}"
                candidates.append(url)

    # ۲) og:url یا canonical
    for meta in soup.find_all("meta"):
        prop = meta.get("property", "")
        rel = meta.get("rel", [])
        if isinstance(rel, list):
            rel = rel[0] if rel else ""
        
        if prop in ("og:url", "twitter:url") or rel == "canonical":
            url = meta.get("content") or meta.get("href")
            if url:
                if url.startswith("//"):
                    url = "https:" + url
                candidates.append(url)

    # ۳) لینک‌های <a> که شامل snapshot pattern باشند
    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href:
            continue
        
        # الگوی snapshot: /[domain]/[timestamp]/[original_url]
        if re.search(r"/\d{14}/", href) or re.search(r"/\d{8}/", href):
            if href.startswith("//"):
                href = "https:" + href
            elif href.startswith("/"):
                href = f"https://{domain}{href}"
            candidates.append(href)

    # ۴) هر لینکی که روی archive.* باشد و به نظر snapshot است
    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href:
            continue
        if any(arch_dom in href for arch_dom in ARCHIVE_DOMAINS):
            if href.startswith("//"):
                href = "https:" + href
            elif href.startswith("/"):
                href = f"https://{domain}{href}"
            candidates.append(href)

    # تصفیه: فقط URLهای معتبر که /submit، /search، ?q= ندارند
    for url in candidates:
        if isinstance(url, str):
            if "/submit" not in url and "/search" not in url and "?q=" not in url:
                # بررسی اینکه دامنه درست باشد
                if any(dom in url for dom in ARCHIVE_DOMAINS):
                    return url

    return None


# ------------------------------
# تست Archive Domains
# ------------------------------
def test_archive_domain(original_url: str, domain: str) -> str | None:
    """
    برای یک دامنهٔ archive:
    ۱) GET /submit/?url=... را تست می‌کند
    ۲) اگر نتیجه‌ای درست نگرفت، POST /submit/ را می‌زند
    
    برمی‌گرداند: URL اسنپ‌شات یا None
    """
    base = f"https://{domain}"
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": f"https://{domain}/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    # فاز ۱: GET
    try:
        check_url = f"{base}/submit/?url={quote_plus(original_url)}"
        write_log(f"  [{domain}] GET {check_url[:80]}...")
        resp = requests.get(
            check_url,
            headers=headers,
            timeout=TIMEOUT,
            allow_redirects=True,
        )
        write_log(f"  [{domain}] GET status: {resp.status_code}")

        if 200 <= resp.status_code < 400:
            snap = extract_snapshot_url_from_html(resp.text, domain)
            if snap:
                write_log(f"  [{domain}] Found snapshot from GET: {snap}")
                return snap

    except requests.exceptions.Timeout:
        write_log(f"  [{domain}] GET timeout - trying POST")
    except requests.exceptions.ConnectionError as e:
        write_log(f"  [{domain}] GET connection error: {e}")
        return None
    except Exception as e:
        write_log(f"  [{domain}] GET error: {e}")

    # فاز ۲: POST
    try:
        submit_url = f"{base}/submit/"
        write_log(f"  [{domain}] POST to {submit_url}")
        data = {
            "url": original_url,
            "anyway": "1",
        }
        resp = requests.post(
            submit_url,
            data=data,
            headers=headers,
            timeout=TIMEOUT,
            allow_redirects=True,
        )
        write_log(f"  [{domain}] POST status: {resp.status_code}")

        if 200 <= resp.status_code < 400:
            snap = extract_snapshot_url_from_html(resp.text, domain)
            if snap:
                write_log(f"  [{domain}] Found snapshot from POST: {snap}")
                return snap
        else:
            write_log(f"  [{domain}] POST returned {resp.status_code}, skipping extraction")

    except requests.exceptions.Timeout:
        write_log(f"  [{domain}] POST timeout - domain too slow")
    except requests.exceptions.ConnectionError as e:
        write_log(f"  [{domain}] POST connection error: {e}")
    except Exception as e:
        write_log(f"  [{domain}] POST error: {e}")

    return None


def get_archive_snapshot_url(original_url: str) -> str | None:
    """
    برای یک URL اصلی:
    - به ترتیب روی archive.is → archive.ph → archive.md سعی می‌کند
    - اول دامنه‌ای که جواب بدهد، لینک snapshot را برمی‌گرداند
    """
    for domain in ARCHIVE_DOMAINS:
        write_log(f"Trying {domain}...")
        snap = test_archive_domain(original_url, domain)
        if snap:
            write_log(f"✓ Success with {domain}: {snap[:60]}...")
            return snap
        write_log(f"✗ {domain} failed, trying next domain...")
        time.sleep(2)  # مکث کوتاه بین دامنه‌ها

    write_log(f"✗ All archive domains failed for {original_url[:60]}...")
    return None


# ------------------------------
# خواندن فایل خروجی موجود
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
                parts = header.split("]", 1)
                entry["source"] = parts[0][1:]
                entry["title"] = parts[1].strip() if len(parts) > 1 else ""

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

                if all(k in entry for k in ("source", "title", "original_url", "archive_url", "pub_date")):
                    entries.append(entry)

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
    write_log("=== Job Started (archive.is/ph/md prioritized) ===")

    try:
        os.makedirs(REPO_DIR, exist_ok=True)
        ensure_history_file()

        history = load_history()
        write_log(f"History loaded: {len(history)} URLs\n")

        # جمع‌آوری مقالات
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

        ny_html = scrape_newyorker_magazine_html()
        for art in ny_html:
            if art["original_url"] not in history:
                new_articles.append(art)

        write_log(f"\nTotal new articles to archive: {len(new_articles)}\n")

        # آرشیو کردن
        new_archive_entries = []
        new_urls_set = set()
        new_articles.sort(key=lambda a: a["pub_date"])

        for idx, art in enumerate(new_articles, 1):
            write_log(f"\n[{idx}/{len(new_articles)}] Archiving: {art['title'][:60]}...")
            write_log(f"URL: {art['original_url']}\n")
            
            time.sleep(2)  # کمی تاخیر
            arch_url = get_archive_snapshot_url(art["original_url"])
            
            if arch_url:
                new_archive_entries.append({
                    "source": art["source"],
                    "title": art["title"],
                    "original_url": art["original_url"],
                    "archive_url": arch_url,
                    "pub_date": art["pub_date"],
                })
                new_urls_set.add(art["original_url"])
                write_log(f"✓ Archived successfully\n")
            else:
                write_log(f"✗ Could not archive from any domain\n")

        if new_urls_set:
            save_history(new_urls_set)
            write_log(f"History updated: +{len(new_urls_set)} URLs\n")

        # ترکیب و نوشتن
        cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
        existing = load_existing_output()
        final_entries = []
        kept_urls = set()

        for e in new_archive_entries:
            final_entries.append(e)
            kept_urls.add(e["original_url"])

        for e in existing:
            if e["original_url"] not in kept_urls:
                try:
                    pub_dt = datetime.fromisoformat(e["pub_date"])
                    if pub_dt >= cutoff:
                        final_entries.append(e)
                        kept_urls.add(e["original_url"])
                except Exception:
                    pass

        final_entries.sort(key=lambda e: e["pub_date"], reverse=True)

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

        write_log(f"\n=== Output written with {len(final_entries)} entries ===")

    except Exception as e:
        write_log(f"FATAL ERROR: {e}")
        raise
    finally:
        write_log("=== Job Finished ===")


if __name__ == "__main__":
    main()
