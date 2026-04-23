import requests
from bs4 import BeautifulSoup
import feedparser
import time
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, quote_plus

# --- Configuration ---
REPO_DIR = "articles"
LOG_FILE = "log.txt"
HISTORY_FILE = "processed_urls.txt"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
TIMEOUT = 25
ARCHIVE_SUBMIT_URL = "https://archive.is/submit/"
MAX_AGE_HOURS = 48   # نگهداری ۴۸ ساعت

SOURCES = [
    {
        "name": "Foreign_Affairs",
        "rss": "https://www.foreignaffairs.com/rss.xml",
        "base_url": "https://www.foreignaffairs.com"
    },
    {
        "name": "Foreign_Policy",
        "rss": "https://foreignpolicy.com/feed/",
        "base_url": "https://foreignpolicy.com"
    },
    {
        "name": "New_Yorker_Magazine",
        "rss": "https://www.newyorker.com/feed/everything",
        "base_url": "https://www.newyorker.com",
        "url_filter": "/magazine/"
    }
]

def write_log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")
    print(f"LOG: {msg}")

def load_history():
    if not os.path.exists(HISTORY_FILE):
        return set()
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

def save_history(new_urls):
    history = load_history()
    history.update(new_urls)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        for url in sorted(history):
            f.write(url + "\n")
    return history

def parse_date_rfc2822(date_str):
    from email.utils import parsedate_to_datetime
    try:
        dt = parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except:
        return None

def fetch_from_rss(rss_url, name, url_filter=None):
    articles = []
    write_log(f"Fetching RSS: {rss_url}")
    try:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/xml, text/xml, */*"
        }
        r = requests.get(rss_url, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        feed = feedparser.parse(r.text)
        if feed.bozo and not feed.entries:
            write_log(f"RSS bozo: {feed.bozo_exception}")
        cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
        for entry in feed.entries:
            link = entry.get('link')
            if not link:
                continue
            if url_filter and url_filter not in link:
                continue
            pub_date = None
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
                pub_date = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
            elif hasattr(entry, 'published'):
                pub_date = parse_date_rfc2822(entry.published)
            if pub_date and pub_date >= cutoff:
                articles.append({
                    "source": name,
                    "title": entry.get('title', 'No Title').strip(),
                    "link": link,
                    "pub_date": pub_date.isoformat()
                })
        write_log(f"RSS {name}: {len(articles)} articles after filter.")
    except Exception as e:
        write_log(f"RSS error {name}: {e}")
    return articles

def scrape_newyorker_magazine_html():
    url = "https://www.newyorker.com/magazine"
    articles = []
    try:
        headers = {"User-Agent": USER_AGENT}
        r = requests.get(url, headers=headers, timeout=TIMEOUT)
        soup = BeautifulSoup(r.text, 'html.parser')
        cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
        for a in soup.select('a[data-link-type="article"], a[href*="/magazine/"]'):
            href = a.get('href')
            if not href:
                continue
            full_url = urljoin("https://www.newyorker.com", href)
            if '/magazine/' not in full_url:
                continue
            m = re.search(r'/magazine/(\d{4})/(\d{2})/(\d{2})/', full_url)
            if m:
                y, mth, d = map(int, m.groups())
                pub_date = datetime(y, mth, d, tzinfo=timezone.utc)
                if pub_date >= cutoff:
                    articles.append({
                        "source": "New_Yorker_Magazine",
                        "title": a.get_text(strip=True) or "Magazine Article",
                        "link": full_url,
                        "pub_date": pub_date.isoformat()
                    })
        uniq = []
        seen = set()
        for art in articles:
            if art['link'] not in seen:
                seen.add(art['link'])
                uniq.append(art)
        write_log(f"Scraped {len(uniq)} NYer magazine links from HTML.")
        return uniq
    except Exception as e:
        write_log(f"NYer scrape error: {e}")
        return []

def extract_archive_snapshot(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    meta = soup.find("meta", property="og:url")
    if meta and meta.get("content") and "archive.is" in meta["content"]:
        return meta["content"]
    link_tag = soup.select_one('div.THUMBS-BLOCK a')
    if link_tag and link_tag.get('href'):
        href = link_tag['href']
        if href.startswith('/'):
            href = 'https://archive.is' + href
        return href
    inp = soup.find("input", {"name": "url"})
    if inp and inp.get("value") and "archive.is" in inp["value"]:
        return inp["value"]
    return None

def get_real_archive_url(original_url):
    headers = {"User-Agent": USER_AGENT, "Referer": "https://archive.is/"}
    try:
        check_url = f"https://archive.is/submit/?url={quote_plus(original_url)}"
        r = requests.get(check_url, headers=headers, timeout=TIMEOUT)
        if r.status_code == 200:
            snap = extract_archive_snapshot(r.text)
            if snap and "/submit/" not in snap:
                write_log(f"✓ Already archived: {snap}")
                return snap
        data = {"url": original_url, "anyway": "1"}
        r_post = requests.post(ARCHIVE_SUBMIT_URL, data=data, headers=headers, timeout=30)
        if r_post.status_code == 200:
            snap = extract_archive_snapshot(r_post.text)
            if snap and "/submit/" not in snap:
                write_log(f"✓ Newly archived: {snap}")
                return snap
        write_log(f"⚠️ No snapshot for {original_url}")
        return None
    except Exception as e:
        write_log(f"Archive error: {e}")
        return None

def load_existing_output():
    """مقاله‌های باقی‌مانده در فایل فعلی را با چک کردن完整性 برمی‌گرداند"""
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
                entry['source'] = header.split(']')[0][1:]
                entry['title'] = header.split(']', 1)[1].strip() if ']' in header else ""
                # خواندن خطوط بعدی
                if i+1 < len(lines) and lines[i+1].startswith("Published:"):
                    entry['pub_date'] = lines[i+1].replace("Published:", "").strip()
                    i += 1
                if i+1 < len(lines) and lines[i+1].startswith("Original:"):
                    entry['original_url'] = lines[i+1].replace("Original:", "").strip()
                    i += 1
                if i+1 < len(lines) and lines[i+1].startswith("Archive :"):
                    entry['archive_url'] = lines[i+1].replace("Archive :", "").strip()
                    i += 1
                # رد شدن از خط جداکننده
                if i+1 < len(lines) and lines[i+1].startswith("---"):
                    i += 1
                # فقط در صورتی ذخیره می‌کنیم که همهٔ فیلدها موجود باشند
                if all(k in entry for k in ('source', 'title', 'original_url', 'archive_url', 'pub_date')):
                    entries.append(entry)
                else:
                    write_log(f"Skipping incomplete entry starting: {line}")
            i += 1
    except Exception as e:
        write_log(f"Error reading existing output: {e}")
        return []   # اگر فایل خراب بود، از صفر شروع کن
    return entries

def main():
    write_log("=== Job Started (v4) ===")
    try:
        os.makedirs(REPO_DIR, exist_ok=True)
        history = load_history()
        write_log(f"History: {len(history)} URLs")

        new_articles = []
        for src in SOURCES:
            arts = fetch_from_rss(src["rss"], src["name"], src.get("url_filter"))
            for art in arts:
                if art["link"] not in history:
                    new_articles.append({
                        "source": src["name"],
                        "title": art["title"],
                        "original_url": art["link"],
                        "pub_date": art["pub_date"]
                    })

        ny_extra = scrape_newyorker_magazine_html()
        for art in ny_extra:
            if art["link"] not in history:
                new_articles.append(art)

        write_log(f"Total new articles to archive: {len(new_articles)}")

        new_archive_entries = []
        new_urls_set = set()
        for art in new_articles:
            write_log(f"Archiving: {art['original_url']}")
            time.sleep(4)   # کمی بیشتر صبر کن
            arch_url = get_real_archive_url(art["original_url"])
            if arch_url:
                new_archive_entries.append({
                    "source": art["source"],
                    "title": art["title"],
                    "original_url": art["original_url"],
                    "archive_url": arch_url,
                    "pub_date": art["pub_date"]
                })
                new_urls_set.add(art["original_url"])
            else:
                write_log(f"Failed: {art['original_url']}")

        if new_urls_set:
            save_history(new_urls_set)

        # ساختن لیست نهایی (تازه‌ها + قدیمی‌های معتبر)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
        existing = load_existing_output()
        final_entries = []
        kept_urls = set()
        # اول تازه‌ها را اضافه کن
        for e in new_archive_entries:
            final_entries.append(e)
            kept_urls.add(e['original_url'])
        # سپس قدیمی‌هایی که تاریخشان هنوز در بازه است و duplicate نیستند
        for e in existing:
            if e['original_url'] in kept_urls:
                continue
            try:
                pub_dt = datetime.fromisoformat(e['pub_date'])
                if pub_dt >= cutoff:
                    final_entries.append(e)
                    kept_urls.add(e['original_url'])
            except:
                pass

        out_path = os.path.join(REPO_DIR, "latest_archives.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"# Last Run: {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC\n")
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
        write_log(f"FATAL ERROR: {str(e)}")
        raise   # بازهم خطا را بالا می‌دهد تا workflow فیل شود، ولی لاگ ذخیره شده
    write_log("=== Job Finished ===")

if __name__ == "__main__":
    main()
