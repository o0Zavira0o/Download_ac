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
# تنظیمات اصلی
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
MAX_AGE_HOURS = 48           # نگه‌داشتن ۴۸ ساعت آخر
REQUEST_DELAY = 5            # مکث بین مقاله‌ها (ثانیه)

ARCHIVE_DOMAINS = [
    "archive.is",
    "archive.ph",
    "archive.md",
]

# وضعیت هر دامنه در این اجرای workflow
DOMAIN_STATUS = {
    dom: {"enabled": True, "reason": None} for dom in ARCHIVE_DOMAINS
}

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
# تاریخچه
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
# RSS helpers
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
    write_log(f"Fetching RSS for {source_name}: {rss_url}")

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
# New Yorker HTML scrape
# ------------------------------
def scrape_newyorker_magazine_html() -> list:
    url = "https://www.newyorker.com/magazine"
    articles = []
    write_log("Scraping New Yorker magazine HTML page...")

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
            if art["original_url"] not in seen:
                seen.add(art["original_url"])
                uniq.append(art)

        write_log(f"New Yorker HTML: {len(uniq)} recent magazine articles.")
        return uniq

    except Exception as e:
        write_log(f"New Yorker HTML error: {e}")
        return []


# ------------------------------
# Archive helpers
# ------------------------------
def disable_domain(domain: str, reason: str) -> None:
    st = DOMAIN_STATUS.get(domain)
    if st and st["enabled"]:
        st["enabled"] = False
        st["reason"] = reason
        write_log(f"!! Disabling {domain} for this run: {reason}")


def normalize_snapshot_url(domain: str, url: str) -> str:
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = f"https://{domain}{url}"
    return url


def is_short_snapshot_url(domain: str, url: str) -> bool:
    url = normalize_snapshot_url(domain, url)
    p = urlparse(url)
    if domain not in p.netloc:
        return False
    if "/submit" in p.path or "/search" in p.path:
        return False
    m = re.match(r"^/([A-Za-z0-9]{3,12})(?:[/?#]|$)", p.path)
    return bool(m)


def is_long_snapshot_url(domain: str, url: str) -> bool:
    url = normalize_snapshot_url(domain, url)
    p = urlparse(url)
    if domain not in p.netloc:
        return False
    if "/submit" in p.path or "/search" in p.path:
        return False
    m = re.match(r"^/\d{8,14}/https?://", p.path)
    return bool(m)


def fetch_url_from_domain(domain: str, full_url: str, purpose: str):
    """
    یک GET ساده روی دامنهٔ مشخص؛
    روی 429، 5xx، timeout و connection error آن دامنه را برای باقی اجرای فعلی غیرفعال می‌کند.
    """
    if not DOMAIN_STATUS[domain]["enabled"]:
        return None

    headers = {
        "User-Agent": USER_AGENT,
        "Referer": f"https://{domain}/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    write_log(f"  [{domain}] {purpose}: {full_url[:120]}")

    try:
        resp = requests.get(
            full_url,
            headers=headers,
            timeout=TIMEOUT,
            allow_redirects=True,
        )
    except requests.exceptions.Timeout:
        disable_domain(domain, f"timeout during {purpose}")
        return None
    except requests.exceptions.ConnectionError as e:
        disable_domain(domain, f"connection error during {purpose}: {e}")
        return None
    except Exception as e:
        disable_domain(domain, f"unexpected error during {purpose}: {e}")
        return None

    status = resp.status_code
    write_log(f"  [{domain}] {purpose} status: {status}")

    if status == 429:
        disable_domain(domain, f"429 Too Many Requests during {purpose}")
        return None
    if 500 <= status < 600:
        disable_domain(domain, f"{status} server error during {purpose}")
        return None
    if status >= 400:
        # مثل 404 یا 403: دامنه هنوز قابل استفاده است، فقط این URL اسنپ‌شات ندارد.
        write_log(f"  [{domain}] {purpose} returned {status}, continuing without disabling domain.")
        return None

    return resp


def extract_snapshot_from_response(domain: str, resp, original_url: str) -> str | None:
    """
    سعی می‌کند:
    ۱) اگر خود resp.url از نوع snapshot است، همان را برگرداند.
    ۲) اگر نه، HTML را parse کند و از آن اسنپ‌شات‌ها را بیرون بکشد.
    """
    final_url = resp.url
    if is_short_snapshot_url(domain, final_url) or is_long_snapshot_url(domain, final_url):
        snap = normalize_snapshot_url(domain, final_url)
        write_log(f"  [{domain}] Final URL is snapshot: {snap}")
        return snap

    # اگر به صفحهٔ نتایج یا صفحهٔ فرم رسیدیم، HTML را بررسی می‌کنیم
    snap = extract_snapshot_url_from_html(resp.text, domain, original_url)
    if snap:
        write_log(f"  [{domain}] Snapshot found in HTML: {snap}")
        return snap

    return None


def extract_snapshot_url_from_html(html_content: str, domain: str, original_url: str) -> str | None:
    """
    HTML برگشتی از archive.{is,ph,md} را parse می‌کند و دنبال لینک آخرین snapshot می‌گردد.
    منطق:
      - لینک‌هایی که path شبیه /maUkD دارند (کد کوتاه)
      - اگر نبود، لینک‌های timestampی مثل /20260428123456/https://...
    """
    soup = BeautifulSoup(html_content, "html.parser")

    short_candidates: list[str] = []
    long_candidates: list[str] = []

    for a in soup.find_all("a", href=True):
        raw_href = a.get("href", "").strip()
        if not raw_href:
            continue

        url = normalize_snapshot_url(domain, raw_href)
        if domain not in url:
            continue

        p = urlparse(url)
        path = p.path or "/"

        if "/submit" in path or "/search" in path:
            continue

        if re.match(r"^/([A-Za-z0-9]{3,12})(?:[/?#]|$)", path):
            if url not in short_candidates:
                short_candidates.append(url)
        elif re.match(r"^/\d{8,14}/https?://", path):
            if url not in long_candidates:
                long_candidates.append(url)

    if short_candidates:
        # فرض: جدیدترین snapshot بالای لیست است
        return short_candidates[0]
    if long_candidates:
        return long_candidates[0]

    return None


def get_latest_snapshot_from_domain(domain: str, original_url: str) -> str | None:
    """
    روی یک دامنه:
      ۱) سعی می‌کند GET به https://domain/<original_url> (مثل چیزی که دستی می‌زنی).
      ۲) اگر از آن چیزی درنیامد، GET به https://domain/search/?q=<original_url>.
    """
    if not DOMAIN_STATUS[domain]["enabled"]:
        return None

    # مرحله ۱: مستقیم /<original_url>
    direct_url = f"https://{domain}/{original_url}"
    resp = fetch_url_from_domain(domain, direct_url, "direct lookup")
    if resp:
        snap = extract_snapshot_from_response(domain, resp, original_url)
        if snap:
            return snap

    # مرحله ۲: صفحهٔ search
    search_url = f"https://{domain}/search/?q={quote_plus(original_url)}"
    resp = fetch_url_from_domain(domain, search_url, "search page")
    if resp:
        snap = extract_snapshot_from_response(domain, resp, original_url)
        if snap:
            return snap

    return None


def get_archive_snapshot_url(original_url: str) -> str | None:
    """
    تابع اصلی آرشیو:
      - به ترتیب روی archive.is → archive.ph → archive.md تست می‌کند.
      - اولین دامنه‌ای که snapshot معتبر برگرداند، نتیجهٔ نهایی است.
      - اگر هیچ‌کدام نشد، در لاگ توضیح می‌دهد که چه شد.
    """
    for domain in ARCHIVE_DOMAINS:
        if not DOMAIN_STATUS[domain]["enabled"]:
            write_log(f"[{domain}] disabled earlier, skipping.")
            continue

        write_log(f"Trying domain {domain} for URL: {original_url}")
        snap = get_latest_snapshot_from_domain(domain, original_url)
        if snap:
            write_log(f"✓ Success on {domain}: {snap}")
            return snap

        write_log(f"✗ No snapshot via {domain}, checking next domain...\n")
        time.sleep(2)  # مکث کوتاه بین دامنه‌ها

    # اگر هیچ دامنه‌ای جواب نداد، دلیل غیرفعال شدن‌ها را لاگ کن
    for dom, st in DOMAIN_STATUS.items():
        if not st["enabled"] and st["reason"]:
            write_log(f"Domain {dom} was disabled because: {st['reason']}")

    return None


# ------------------------------
# خواندن خروجی موجود
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

                if all(
                    k in entry
                    for k in ("source", "title", "original_url", "archive_url", "pub_date")
                ):
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
    write_log("=== Job Started (archive.is / archive.ph / archive.md) ===")

    try:
        os.makedirs(REPO_DIR, exist_ok=True)
        ensure_history_file()

        history = load_history()
        write_log(f"History loaded: {len(history)} URLs\n")

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

        write_log(f"\nTotal new articles to process: {len(new_articles)}\n")

        # ۲) آرشیو در archive.is / .ph / .md
        new_archive_entries = []
        new_urls_set = set()
        new_articles.sort(key=lambda a: a["pub_date"])

        for idx, art in enumerate(new_articles, 1):
            write_log(f"[{idx}/{len(new_articles)}] Article: {art['title'][:80]}")
            write_log(f"Original URL: {art['original_url']}")
            time.sleep(REQUEST_DELAY)

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
                write_log("Result: SUCCESS\n")
            else:
                write_log("Result: FAILED (no snapshot)\n")

        if new_urls_set:
            save_history(new_urls_set)
            write_log(f"History updated: +{len(new_urls_set)} URLs\n")

        # ۳) ترکیب با خروجی قبلی و حذف مقالات قدیمی‌تر از ۴۸ ساعت
        cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
        existing = load_existing_output()
        final_entries = []
        kept_urls = set()

        for e in new_archive_entries:
            final_entries.append(e)
            kept_urls.add(e["original_url"])

        for e in existing:
            if e["original_url"] in kept_urls:
                continue
            try:
                pub_dt = datetime.fromisoformat(e["pub_date"])
                if pub_dt >= cutoff:
                    final_entries.append(e)
                    kept_urls.add(e["original_url"])
            except Exception:
                pass

        final_entries.sort(key=lambda e: e["pub_date"], reverse=True)

        # ۴) نوشتن فایل خروجی
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

        write_log(f"=== Output written with {len(final_entries)} entries ===")

    except Exception as e:
        write_log(f"FATAL ERROR: {e}")
        raise
    finally:
        write_log("=== Job Finished ===")


if __name__ == "__main__":
    main()
