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
TIMEOUT = 30
MAX_AGE_HOURS = 48  # فقط ۴۸ ساعت آخر

ARCHIVE_DOMAINS = [
    "archive.is",
    "archive.ph",
    "archive.md",
    "archive.today",
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
# کمک‌های عمومی و لاگ
# ------------------------------
def write_log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def ensure_history_file() -> None:
    """مطمئن می‌شود فایل history وجود دارد (برای git add)."""
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
# خواندن تاریخ از RSS
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
    """
    خروجی: مثل RSS، با کلید original_url
    """
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

            # تاریخ را از URL درمی‌آوریم /magazine/YYYY/MM/DD/
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

        # حذف تکراری‌ها
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
# تشخیص URL اسنپ‌شات آرشیو
# ------------------------------
def is_snapshot_url(url: str) -> bool:
    """
    تلاش برای تشخیص اینکه آیا URL یک اسنپ‌شات واقعی است
    و نه صفحهٔ جستجو/submit.
    """
    if "archive." not in url:
        return False
    if "/submit" in url:
        return False
    if "/search" in url:
        return False
    if "?q=" in url:
        return False
    if "?run=" in url:
        return False

    # نخواهیم خود هوم‌پیج یا چیزی شبیه آن را
    stripped = url.rstrip("/")
    for dom in ARCHIVE_DOMAINS:
        home = f"https://{dom}"
        if stripped == home:
            return False

    return True


def extract_archive_snapshot(html_content: str) -> str | None:
    """
    تلاش برای پیدا کردن URL اسنپ‌شات در HTML برگردانده شده از archive.*
    """
    soup = BeautifulSoup(html_content, "html.parser")

    # 1) meta og:url
    meta = soup.find("meta", attrs={"property": "og:url"})
    if meta and meta.get("content"):
        candidate = meta["content"].strip()
        if candidate.startswith("//"):
            candidate = "https:" + candidate
        if is_snapshot_url(candidate):
            return candidate

    # 2) input با name="url"
    inp = soup.find("input", attrs={"name": "url"})
    if inp and inp.get("value"):
        candidate = inp["value"].strip()
        if candidate.startswith("//"):
            candidate = "https:" + candidate
        if is_snapshot_url(candidate):
            return candidate

    # 3) لینک‌ها داخل بخش نتیجه / thumbnail ها
    # الگوی قدیمی archive.is
    thumbs = soup.select("div.THUMBS-BLOCK a[href]")
    for a in thumbs:
        href = a["href"]
        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            # دامنه را از og:url یا یک دامنهٔ پیش‌فرض حدس بزنیم
            href = "https://archive.is" + href
        if is_snapshot_url(href):
            return href

    # 4) سایر لینک‌هایی که به archive.* اشاره می‌کنند
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            # اینجا دقیق نمی‌دانیم کدام دامنه است؛ صرفاً رد می‌کنیم
            continue
        if is_snapshot_url(href):
            return href

    return None


def get_real_archive_url(original_url: str) -> str | None:
    """
    برای original_url:
    - روی هر دامنهٔ archive.* اول چک می‌کند اسنپ‌شات موجود هست یا نه
    - اگر نبود، تلاش می‌کند یک اسنپ‌شات جدید بسازد
    - اولین اسنپ‌شات معتبر را برمی‌گرداند
    """
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": "https://archive.is/",
    }

    for domain in ARCHIVE_DOMAINS:
        base = f"https://{domain}"
        try:
            # 1) چک اسنپ‌شات موجود
            check_url = f"{base}/submit/?url={quote_plus(original_url)}"
            write_log(f"Checking archive snapshot on {domain}...")
            r = requests.get(check_url, headers=headers, timeout=TIMEOUT)
            if r.status_code == 200:
                snap = extract_archive_snapshot(r.text)
                if snap:
                    write_log(f"Existing snapshot on {domain}: {snap}")
                    return snap
        except Exception as e:
            write_log(f"{domain} check error: {e}")

        # 2) ایجاد اسنپ‌شات اگر پیدا نشد
        try:
            submit_url = f"{base}/submit/"
            data = {"url": original_url, "anyway": "1"}
            write_log(f"Submitting to {domain}...")
            r_post = requests.post(
                submit_url, data=data, headers=headers, timeout=TIMEOUT
            )
            if r_post.status_code == 200:
                snap = extract_archive_snapshot(r_post.text)
                if snap:
                    write_log(f"New snapshot on {domain}: {snap}")
                    return snap
        except Exception as e:
            write_log(f"{domain} submit error: {e}")

    write_log(f"No valid snapshot found for {original_url}")
    return None


# ------------------------------
# خواندن خروجی موجود
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

                # Published
                if i + 1 < len(lines) and lines[i + 1].startswith("Published:"):
                    entry["pub_date"] = (
                        lines[i + 1].replace("Published:", "").strip()
                    )
                    i += 1

                # Original
                if i + 1 < len(lines) and lines[i + 1].startswith("Original:"):
                    entry["original_url"] = (
                        lines[i + 1].replace("Original:", "").strip()
                    )
                    i += 1

                # Archive
                if i + 1 < len(lines) and lines[i + 1].startswith("Archive :"):
                    entry["archive_url"] = (
                        lines[i + 1].replace("Archive :", "").strip()
                    )
                    i += 1

                # جداکننده
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
    write_log("=== Job Started (fetcher_v2) ===")
    try:
        os.makedirs(REPO_DIR, exist_ok=True)
        ensure_history_file()

        history = load_history()
        write_log(f"History loaded: {len(history)} URLs")

        # ۱) گرفتن مقالات RSS
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

        # ۲) HTML برای نیویورکر مجله
        ny_html_articles = scrape_newyorker_magazine_html()
        for art in ny_html_articles:
            if art["original_url"] not in history:
                new_articles.append(art)

        write_log(f"Total new articles to process: {len(new_articles)}")

        # ۳) آرشیو کردن در archive.*
        new_archive_entries = []
        new_urls_set = set()

        # (اختیاری) مرتب‌سازی بر اساس تاریخ برای لاگ مرتب‌تر
        new_articles.sort(key=lambda a: a["pub_date"])

        for art in new_articles:
            write_log(f"Archiving: {art['original_url']}")
            time.sleep(4)  # برای جلوگیری از rate-limit
            arch_url = get_real_archive_url(art["original_url"])
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
                write_log(f"Failed to archive: {art['original_url']}")

        if new_urls_set:
            save_history(new_urls_set)
            write_log(f"History updated, +{len(new_urls_set)} URLs")

        # ۴) ترکیب با خروجی قبلی و حذف قدیمی‌ها
        cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
        existing_entries = load_existing_output()
        final_entries = []
        kept_urls = set()

        # اول ورودی‌های جدید
        for e in new_archive_entries:
            final_entries.append(e)
            kept_urls.add(e["original_url"])

        # بعد، قدیمی‌هایی که هنوز در بازه زمانی هستند و تکراری نیستند
        for e in existing_entries:
            if e["original_url"] in kept_urls:
                continue
            try:
                pub_dt = datetime.fromisoformat(e["pub_date"])
                if pub_dt >= cutoff:
                    final_entries.append(e)
                    kept_urls.add(e["original_url"])
            except Exception:
                # اگر تاریخ خراب بود، بی‌خیال
                continue

        # مرتب‌سازی نهایی (جدیدترها بالا)
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
