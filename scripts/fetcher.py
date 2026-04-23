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
MAX_AGE_HOURS = 48  # مقالات با عمر کمتر از 48 ساعت

SOURCES = [
    {
        "name": "Foreign_Affairs",
        "rss": "https://www.foreignaffairs.com/rss.xml",  # فید اصلی
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
        "url_filter": "/magazine/"  # فقط مواردی که این عبارت در URL باشد
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
        headers = {"User-Agent": USER_AGENT}
        # برخی فیدها نیاز به Accept دارند
        r = requests.get(rss_url, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        feed = feedparser.parse(r.text)
        if feed.bozo:
            write_log(f"RSS bozo: {feed.bozo_exception}")
        cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
        for entry in feed.entries:
            link = entry.get('link')
            if not link:
                continue
            if url_filter and url_filter not in link:
                continue  # مثلاً New Yorker فقط magazine
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
    """اسکرپ مستقیم صفحه مجله برای اطمینان از دریافت مقالات"""
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
            # تلاش برای استخراج تاریخ از URL (مثلاً /magazine/2026/04/22/)
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
        # حذف تکراری
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
    """بگرد دنبال لینک اصلی اسنپ‌شات در HTML برگشتی از archive.is"""
    soup = BeautifulSoup(html_content, 'html.parser')
    # 1. متای og:url
    meta = soup.find("meta", property="og:url")
    if meta and meta.get("content") and "archive.is" in meta["content"]:
        return meta["content"]
    # 2. لینک داخل div.THUMBS-BLOCK
    link_tag = soup.select_one('div.THUMBS-BLOCK a')
    if link_tag and link_tag.get('href'):
        href = link_tag['href']
        if href.startswith('/'):
            href = 'https://archive.is' + href
        return href
    # 3. لینک در input با نام url
    inp = soup.find("input", {"name": "url"})
    if inp and inp.get("value") and "archive.is" in inp["value"]:
        return inp["value"]
    return None

def get_real_archive_url(original_url):
    """تلاش برای دریافت لینک واقعی archive.is (نه submit)"""
    headers = {"User-Agent": USER_AGENT, "Referer": "https://archive.is/"}
    try:
        # اول چک کنیم قبلاً آرشیو شده؟
        check_url = f"https://archive.is/submit/?url={quote_plus(original_url)}"
        r = requests.get(check_url, headers=headers, timeout=TIMEOUT)
        if r.status_code == 200:
            snap = extract_archive_snapshot(r.text)
            if snap and "/submit/" not in snap:
                write_log(f"✓ Already archived: {snap}")
                return snap

        # اگر نشد، submit کنیم
        data = {"url": original_url, "anyway": "1"}
        r_post = requests.post(ARCHIVE_SUBMIT_URL, data=data, headers=headers, timeout=30)
        if r_post.status_code == 200:
            snap = extract_archive_snapshot(r_post.text)
            if snap and "/submit/" not in snap:
                write_log(f"✓ Newly archived: {snap}")
                return snap
        # اگر همش شکست خورد، برمیگردیم None
        write_log(f"⚠️ Could not extract snapshot for {original_url}")
        return None
    except Exception as e:
        write_log(f"Archive error: {e}")
        return None

def load_existing_output():
    """فایل latest_archives.txt موجود را می‌خواند و مقالات فعلی را استخراج می‌کند"""
    out_path = os.path.join(REPO_DIR, "latest_archives.txt")
    if not os.path.exists(out_path):
        return []
    entries = []
    with open(out_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    i = 0
    while i < len(lines):
        if lines[i].startswith("[") and "]" in lines[i]:
            entry = {}
            header = lines[i].strip()
            entry['source'] = header.split(']')[0][1:]
            entry['title'] = header.split(']',1)[1].strip()
            if i+1 < len(lines) and lines[i+1].startswith("Published:"):
                pub_str = lines[i+1].replace("Published:", "").strip()
                entry['pub_date'] = pub_str
                i += 1
            if i+1 < len(lines) and lines[i+1].startswith("Original:"):
                orig = lines[i+1].replace("Original:", "").strip()
                entry['original_url'] = orig
                i += 1
            if i+1 < len(lines) and lines[i+1].startswith("Archive :"):
                arch = lines[i+1].replace("Archive :", "").strip()
                entry['archive_url'] = arch
                i += 1
            if i+1 < len(lines) and lines[i+1].startswith("---"):
                i += 1
            entries.append(entry)
        i += 1
    return entries

def main():
    write_log("=== Job Started (v3 archive.is) ===")
    os.makedirs(REPO_DIR, exist_ok=True)

    history = load_history()
    write_log(f"History: {len(history)} URLs")

    new_articles = []

    # 1. RSS منابع
    for src in SOURCES:
        url_filter = src.get("url_filter", None)
        arts = fetch_from_rss(src["rss"], src["name"], url_filter)
        for art in arts:
            if art["link"] not in history:
                new_articles.append({
                    "source": src["name"],
                    "title": art["title"],
                    "original_url": art["link"],
                    "pub_date": art["pub_date"]
                })

    # 2. اسکرپ اضافی نیویورکر (جهت اطمینان)
    ny_extra = scrape_newyorker_magazine_html()
    for art in ny_extra:
        if art["link"] not in history:
            new_articles.append(art)

    write_log(f"Total new articles to archive: {len(new_articles)}")

    # 3. آرشیو کردن
    new_archive_entries = []
    new_urls_set = set()
    for art in new_articles:
        write_log(f"Archiving: {art['original_url']}")
        time.sleep(3)  # احترام
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

    # 4. بازسازی فایل خروجی (فقط مقالات ۴۸ ساعت گذشته)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
    final_entries = []
    # ابتدا مقالات جدید رو اضافه کن
    final_entries.extend(new_archive_entries)
    # حالا از فایل موجود آنهایی که هنوز در ۴۸ ساعت هستند و duplicate نیستند را نگه داریم
    existing = load_existing_output()
    existing_urls = {e['original_url'] for e in existing}
    for old in existing:
        # اگر url قدیمی در بین جدیدها نیست و هنوز تاریخش در بازه‌ست
        if old['original_url'] not in new_urls_set:
            try:
                pub_dt = datetime.fromisoformat(old['pub_date'])
                if pub_dt >= cutoff:
                    # نیاز به آرشیو مجدد؟ احتمالاً لینک آرشیو همان است که ذخیره شده
                    final_entries.append(old)
            except:
                pass

    # نوشتن فایل
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
    write_log("=== Job Finished ===")

if __name__ == "__main__":
    main()
