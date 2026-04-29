#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
from datetime import datetime, timedelta, timezone

import feedparser
import requests

# ------------------------------
# تنظیمات
# ------------------------------
REPO_DIR = "articles"
OUTPUT_FILE = "latest_articles.txt"
LOG_FILE = "log.txt"
HISTORY_FILE = "processed_urls.txt"
STATE_FILE = "source_state.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
TIMEOUT = 20

# فقط مقالات ۳ روز اخیر در خروجی بمانند
MAX_AGE_HOURS = 72

SOURCES = [
    {
        "name": "Foreign_Affairs",
        "rss": "https://www.foreignaffairs.com/rss.xml",
        "min_interval_hours": 2,   # هر ۲ ساعت
        "url_filter": None,
    },
    {
        "name": "Foreign_Policy",
        "rss": "https://foreignpolicy.com/feed/",
        "min_interval_hours": 2,   # هر ۲ ساعت
        "url_filter": None,
    },
    {
        "name": "New_Yorker_Magazine",
        "rss": "https://www.newyorker.com/feed/everything",
        "min_interval_hours": 72,  # هر ۳ روز یک‌بار
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


def save_history(additional_urls: set) -> None:
    history = load_history()
    history.update(additional_urls)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        for url in sorted(history):
            f.write(url + "\n")


# ------------------------------
# وضعیت آخرین چک هر منبع
# ------------------------------
def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


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


def fetch_from_rss(source: dict) -> list:
    """
    خروجی هر آیتم:
    {
      "source": source['name'],
      "title": "...",
      "link": "...",
      "pub_date": "<ISO8601>",
    }
    """
    name = source["name"]
    rss_url = source["rss"]
    url_filter = source.get("url_filter")

    articles = []
    write_log(f"Fetching RSS for {name}: {rss_url}")

    try:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        }
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
                    "source": name,
                    "title": title,
                    "link": link,
                    "pub_date": pub_date.isoformat(),
                }
            )

        write_log(f"RSS {name}: {len(articles)} recent articles.")
    except Exception as e:
        write_log(f"RSS error [{name}]: {e}")

    return articles


# ------------------------------
# خواندن خروجی موجود
# ------------------------------
def load_existing_output() -> list:
    out_path = os.path.join(REPO_DIR, OUTPUT_FILE)
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

                if i + 1 < len(lines) and lines[i + 1].startswith("Link:"):
                    entry["link"] = lines[i + 1].replace("Link:", "").strip()
                    i += 1

                if i + 1 < len(lines) and lines[i + 1].startswith("---"):
                    i += 1

                if all(k in entry for k in ("source", "title", "link", "pub_date")):
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
    write_log("=== Job Started (articles only, no archive.*) ===")

    try:
        os.makedirs(REPO_DIR, exist_ok=True)
        ensure_history_file()

        history = load_history()
        state = load_state()
        now_utc = datetime.now(timezone.utc)

        write_log(f"History loaded: {len(history)} URLs")
        write_log(f"State loaded for sources: {list(state.keys())}\n")

        new_articles = []
        new_urls_set = set()

        # ۱) چک‌کردن منابع (با درنظرگرفتن min_interval_hours)
        for src in SOURCES:
            name = src["name"]
            min_int = src["min_interval_hours"]

            last_ts_str = state.get(name)
            if last_ts_str:
                try:
                    last_ts = datetime.fromisoformat(last_ts_str)
                except Exception:
                    last_ts = None
            else:
                last_ts = None

            if last_ts:
                delta_h = (now_utc - last_ts).total_seconds() / 3600.0
                if delta_h < min_int:
                    write_log(
                        f"Skipping {name}: last checked {delta_h:.1f} hours ago (min {min_int}h)."
                    )
                    continue

            # این منبع را چک می‌کنیم
            arts = fetch_from_rss(src)
            for art in arts:
                if art["link"] not in history:
                    new_articles.append(art)
                    new_urls_set.add(art["link"])

            # زمان آخرین چک را به‌روزرسانی کن
            state[name] = now_utc.isoformat()

        write_log(f"\nTotal new articles found: {len(new_articles)}")

        # ۲) اگر مقالهٔ جدید داشتیم، تاریخچه را آپدیت کن
        if new_urls_set:
            save_history(new_urls_set)
            write_log(f"History updated (+{len(new_urls_set)} URLs).")

        # ۳) ساختن خروجی نهایی: جدیدها + قدیمی‌هایی که هنوز در بازهٔ ۳ روزه هستند
        cutoff = now_utc - timedelta(hours=MAX_AGE_HOURS)
        existing_entries = load_existing_output()
        final_entries = []
        kept_links = set()

        # اول مقالات جدید
        for art in new_articles:
            final_entries.append(
                {
                    "source": art["source"],
                    "title": art["title"],
                    "link": art["link"],
                    "pub_date": art["pub_date"],
                }
            )
            kept_links.add(art["link"])

        # سپس مقالات قبلی که هنوز در بازه‌اند و تکراری نیستند
        for e in existing_entries:
            if e["link"] in kept_links:
                continue
            try:
                pub_dt = datetime.fromisoformat(e["pub_date"])
                if pub_dt >= cutoff:
                    final_entries.append(e)
                    kept_links.add(e["link"])
            except Exception:
                # اگر تاریخ خراب بود، بی‌خیال آن مقاله می‌شویم
                continue

        # مرتب‌سازی بر اساس تاریخ (جدیدتر بالاتر)
        final_entries.sort(key=lambda x: x["pub_date"], reverse=True)

        # ۴) نوشتن خروجی
        out_path = os.path.join(REPO_DIR, OUTPUT_FILE)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"# Last Run: {now_utc.strftime('%Y-%m-%d %H:%M')} UTC\n")
            f.write(f"# Articles published in last {MAX_AGE_HOURS} hours\n\n")

            if final_entries:
                for e in final_entries:
                    f.write(f"[{e['source']}] {e['title']}\n")
                    f.write(f"Published: {e['pub_date']}\n")
                    f.write(f"Link: {e['link']}\n")
                    f.write("---\n")
            else:
                f.write("No articles in the last 72 hours.\n")

        write_log(f"\nOutput written with {len(final_entries)} entries.")
        save_state(state)
        write_log("Source state saved.")

    except Exception as e:
        write_log(f"FATAL ERROR: {e}")
        raise
    finally:
        write_log("=== Job Finished ===")


if __name__ == "__main__":
    main()
