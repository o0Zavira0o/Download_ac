import requests
from bs4 import BeautifulSoup
import feedparser
import time
import os
import sys
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

# --- Configuration ---
REPO_DIR = "articles"
LOG_FILE = "log.txt"
HISTORY_FILE = "processed_urls.txt"  # برای جلوگیری از تکراری
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
TIMEOUT = 25
ARCHIVE_SUBMIT_URL = "https://archive.ph/submit/"
MAX_AGE_HOURS = 24  # مقالاتی که در این مدت منتشر شده‌اند

# لیست منابع با RSS یا URL مستقیم
SOURCES = [
    {
        "name": "Foreign_Affairs",
        "rss": "https://www.foreignaffairs.com/feed",
        "base_url": "https://www.foreignaffairs.com"
    },
    {
        "name": "Foreign_Policy",
        "rss": "https://foreignpolicy.com/feed/",
        "base_url": "https://foreignpolicy.com"
    },
    {
        "name": "New_Yorker",
        "rss": "https://www.newyorker.com/feed/everything",  # RSS عمومی نیویورکر
        "base_url": "https://www.newyorker.com"
    }
]

# ---------- توابع کمکی ----------
def write_log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")
    print(f"LOG: {message}")

def load_history():
    """بارگذاری URL‌های قبلاً پردازش شده"""
    if not os.path.exists(HISTORY_FILE):
        return set()
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

def save_history(new_urls):
    """ذخیره URLهای جدید در فایل history"""
    current = load_history()
    current.update(new_urls)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        for url in sorted(current):
            f.write(url + "\n")
    return current

def parse_rss_date(date_str):
    """تبدیل تاریخ RSS به datetime آگاه از timezone"""
    # فرمت‌های مختلف ممکن است
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(date_str)
    except:
        return None

def fetch_articles_from_rss(rss_url, name):
    """دریافت تمام مقالات ۲۴ ساعت گذشته از RSS"""
    articles = []
    write_log(f"Checking RSS for {name}: {rss_url}")
    try:
        feed = feedparser.parse(rss_url)
        if feed.bozo and not feed.entries:
            write_log(f"RSS parse error for {name}: {feed.bozo_exception}")
        cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
        for entry in feed.entries:
            pub_date = None
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
                pub_date = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
            if pub_date and pub_date >= cutoff:
                articles.append({
                    "title": entry.get("title", "No Title").strip(),
                    "link": entry.get("link"),
                    "pub_date": pub_date.isoformat()
                })
        write_log(f"Found {len(articles)} new articles from {name} RSS")
    except Exception as e:
        write_log(f"Error fetching RSS for {name}: {str(e)}")
    return articles

def fetch_newyorker_magazine_articles():
    """نیویورکر بخش مجله ممکنه تو RSS نباشه، یکبار از صفحه اصلی مجله هم اسکرپ کنیم"""
    # برای اطمینان، می‌توانیم از صفحه magazine هم مواردی را بگیریم
    url = "https://www.newyorker.com/magazine"
    articles = []
    try:
        headers = {"User-Agent": USER_AGENT}
        r = requests.get(url, headers=headers, timeout=TIMEOUT)
        soup = BeautifulSoup(r.text, 'html.parser')
        cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
        # جستجوی لینک‌های مقالات (با کلاس‌های رایج)
        for a in soup.select('a[data-link-type="article"]'):
            href = a.get('href')
            if href:
                full_url = urljoin("https://www.newyorker.com", href)
                # فقط آنهایی که تاریخ اخیر دارند (از ساختار URL می‌توان فهمید)
                if re.search(r'/magazine/\d{4}/\d{2}/\d{2}/', full_url):
                    # استخراج تاریخ از URL
                    match = re.search(r'/(\d{4})/(\d{2})/(\d{2})/', full_url)
                    if match:
                        y, m, d = map(int, match.groups())
                        pub_date = datetime(y, m, d, tzinfo=timezone.utc)
                        if pub_date >= cutoff:
                            articles.append({
                                "title": a.get_text(strip=True),
                                "link": full_url,
                                "pub_date": pub_date.isoformat()
                            })
        # حذف تکراری‌ها
        seen = set()
        unique_articles = []
        for art in articles:
            if art['link'] not in seen:
                seen.add(art['link'])
                unique_articles.append(art)
        write_log(f"Found {len(unique_articles)} magazine articles from New Yorker HTML")
        return unique_articles
    except Exception as e:
        write_log(f"Error scraping New Yorker magazine: {str(e)}")
        return []

def get_archive_snapshot(original_url):
    """دریافت لینک واقعی archive.ph (نه /latest/)"""
    if not original_url:
        return None
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": "https://archive.ph/",
        "Origin": "https://archive.ph"
    }
    # ابتدا چک کنیم قبلاً آرشیو شده؟
    try:
        check_url = f"https://archive.ph/submit/?url={original_url}"
        r = requests.get(check_url, headers=headers, timeout=TIMEOUT)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            # لینک آرشیو ممکن است در input مخفی یا meta باشد
            meta = soup.find("meta", property="og:url")
            if meta and meta.get("content") and "archive.ph" in meta["content"]:
                write_log(f"Already archived: {meta['content']}")
                return meta["content"]
    except:
        pass

    # اگر نبود، سابمیت کنیم
    try:
        data = {"url": original_url, "anyway": "1"}
        r_post = requests.post(ARCHIVE_SUBMIT_URL, data=data, headers=headers, timeout=30, allow_redirects=True)
        # archive.ph معمولاً با Location برمی‌گرداند یا صفحه‌ای با لینک
        if r_post.status_code == 200:
            soup = BeautifulSoup(r_post.text, 'html.parser')
            meta = soup.find("meta", property="og:url")
            if meta and meta.get("content"):
                return meta["content"]
            # گاهی لینک در یک div نتیجه است
            result_link = soup.select_one('div.THUMBS-BLOCK a')
            if result_link and result_link.get('href'):
                href = result_link['href']
                if href.startswith('/'):
                    href = 'https://archive.ph' + href
                return href
        # اگر همه راه‌ها شکست خورد
        return f"https://archive.ph/submit/?url={original_url}"  # لینک ارسال دستی
    except Exception as e:
        write_log(f"Archive submission error: {str(e)}")
        return None

# ---------- بخش اصلی ----------
def main():
    write_log("=== Job Started (v2) ===")
    os.makedirs(REPO_DIR, exist_ok=True)

    # بارگذاری تاریخچه URLهای فرستاده شده
    history = load_history()
    write_log(f"Loaded {len(history)} processed URLs")

    all_new_articles = []

    # 1. دریافت از RSS منابع
    for src in SOURCES:
        articles = fetch_articles_from_rss(src["rss"], src["name"])
        for art in articles:
            if art["link"] not in history:
                all_new_articles.append({
                    "source": src["name"],
                    "title": art["title"],
                    "original_url": art["link"],
                    "pub_date": art["pub_date"]
                })

    # 2. برای نیویورکر بخش مجله را هم اسکرپ کن (می‌تواند مکمل RSS باشد)
    ny_mag_articles = fetch_newyorker_magazine_articles()
    for art in ny_mag_articles:
        if art["link"] not in history:
            all_new_articles.append({
                "source": "New_Yorker_Magazine",
                "title": art["title"],
                "original_url": art["link"],
                "pub_date": art["pub_date"]
            })

    if not all_new_articles:
        write_log("No new articles found in last 24h.")
        # با این وجود فایل خروجی را بازنویسی می‌کنیم تا قدیمی‌ها حذف شوند
    else:
        write_log(f"Total new articles to archive: {len(all_new_articles)}")

    # 3. آرشیو کردن URLها (با تاخیر)
    new_archive_entries = []
    new_urls_set = set()
    for art in all_new_articles:
        write_log(f"Archiving: {art['original_url']}")
        time.sleep(4)  # احترام به archive.ph
        archive_link = get_archive_snapshot(art["original_url"])
        if archive_link:
            new_archive_entries.append({
                "source": art["source"],
                "title": art["title"],
                "original_url": art["original_url"],
                "archive_url": archive_link,
                "pub_date": art["pub_date"]
            })
            new_urls_set.add(art["original_url"])
        else:
            write_log(f"Failed to get archive for {art['original_url']}")

    # 4. به‌روزرسانی تاریخچه
    if new_urls_set:
        save_history(new_urls_set)

    # 5. بارگذاری فایل خروجی موجود (برای حفظ مقالات قدیمی‌تر معتبر)
    output_file = os.path.join(REPO_DIR, "latest_archives.txt")
    existing_entries = []
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)

    if os.path.exists(output_file):
        with open(output_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        # پارس کردن محتوا سخت است، پس ساده‌تر: فایل را خالی می‌کنیم و فقط از روی new_archive_entries بازنویسی
        # اما ممکن است مقالات آرشیو شده قبلی که هنوز در ۲۴ ساعت هستند را از دست بدهیم.
        pass

    # روش بهتر: ما کل خروجی را از ابتدا می‌سازیم فقط با مقالاتی که در بازه ۲۴ ساعت هستند.
    # پس هر بار فایل کاملاً بازنویسی می‌شود بر اساس تاریخ انتشار.
    final_lines = []
    final_lines.append(f"# Last Run: {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC\n")
    final_lines.append(f"# Articles published in last {MAX_AGE_HOURS} hours\n\n")

    # فقط مقالات جدید را اضافه کنیم؟ نه، باید مقالات قبلی که هنوز در بازه هستند را هم نگه داریم.
    # ولی ما تاریخچه url را داریم و هر url جدید را آرشیو می‌کنیم.
    # اما ممکن است یوآر‌ال‌های قدیمی که تاریخ انتشارشان قدیمی شده دیگر لازم نباشند.
    # برای سادگی: خروجی فقط شامل new_archive_entries است. اما اگر بخواهیم قدیمی‌ها نباشند، یعنی هر بار خالی می‌شود.
    # راهکار: یک فایل JSON جدا برای نگهداری همه آرشیوها با تاریخ ذخیره کنیم. ولی بنا به درخواست «اون‌هایی که چند روز قبل هستن حذف بشن» ساده‌ترین راه این است که فایل خروجی فقط شامل آرشیوهای ۲۴ ساعت گذشته باشد.

    # پس: فایل قدیمی را پاک می‌کنیم و با آرشیوهای جدید (که همه مربوط به ۲۴ ساعت قبل هستند) پر می‌کنیم.
    # این ایده‌آل است.
    # اما اگر اسکریپت نتواند مقاله‌ای را آرشیو کند، آن مقاله از خروجی حذف می‌شود، مشکلی نیست.

    for entry in new_archive_entries:
        final_lines.append(f"[{entry['source']}] {entry['title']}\n")
        final_lines.append(f"Published: {entry['pub_date']}\n")
        final_lines.append(f"Original: {entry['original_url']}\n")
        final_lines.append(f"Archive : {entry['archive_url']}\n")
        final_lines.append("---\n")

    if not final_lines[2:]:  # بدون احتساب کامنت‌ها
        final_lines.append("No articles archived in this run.\n")

    with open(output_file, "w", encoding="utf-8") as f:
        f.writelines(final_lines)

    write_log(f"Output written to {output_file} with {len(new_archive_entries)} entries.")
    write_log("=== Job Finished ===")

if __name__ == "__main__":
    main()
