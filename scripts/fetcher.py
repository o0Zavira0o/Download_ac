import requests
from bs4 import BeautifulSoup
import feedparser
import time
import os
import sys
from datetime import datetime

# --- Configuration ---
REPO_DIR = "articles"
LOG_FILE = "log.txt"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
TIMEOUT = 20
ARCHIVE_SUBMIT_URL = "https://archive.ph/submit/"

# لیست منابع: 
# توی سیستم ملی، RSS از همه بهتر جواب میده.
SOURCES = [
    {
        "name": "Foreign_Affairs",
        "rss": "https://www.foreignaffairs.com/feed",
        "selector": None # از RSS استفاده می‌کنیم
    },
    {
        "name": "Foreign_Policy",
        "rss": "https://foreignpolicy.com/feed/",
        "selector": None
    },
    {
        "name": "New_Yorker_Magazine",
        # New Yorker RSS کار نمیکنه همیشه، باید بگردیم تو صفحه مجله
        "rss": None,
        "url": "https://www.newyorker.com/magazine",
        "selector": "div.River__riverItemContent___2vGZJ h4 a" # کلاس تقریبی (ممکنه تغییر کنه)
    }
]

def write_log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")
    print(f"LOG: {message}")

def get_archive_link(original_url):
    """لینک مقاله رو به archive.ph میده و لینک اسنپ‌شات رو برمی‌گردونه"""
    if not original_url:
        return None
        
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": "https://archive.ph/",
        "Origin": "https://archive.ph"
    }
    data = {
        "url": original_url,
        "anyway": "1" # اگر قبلا ذخیره شده بود، لینکش رو بده
    }
    
    try:
        # اول ببینیم آیا از قبل تو archive بوده؟
        r = requests.get(f"https://archive.ph/submit/", params={"url": original_url}, headers=headers, timeout=TIMEOUT)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            # ورودی مخفی برای لینک مستقیم
            input_tag = soup.find("input", {"name": "url"})
            if input_tag and input_tag.get("value"):
                # ممکنه لینک مستقیم رو تو value بده
                val = input_tag.get("value")
                if "archive.ph" in val:
                    return val
                    
        # اگر لینک مستقیم نبود، پست رو انجام بده (حتی اگر قبلا ذخیره شده باشه)
        r_post = requests.post(ARCHIVE_SUBMIT_URL, data=data, headers=headers, timeout=TIMEOUT)
        if "refresh" in r_post.text.lower():
            # archive.ph معمولا یه ریدایرکت جاوااسکریپتی داره. لینک رو از هدر یا متن درمیاریم
            soup = BeautifulSoup(r_post.text, 'html.parser')
            meta = soup.find("meta", property="og:url")
            if meta and meta.get("content"):
                return meta.get("content")
        
        # Fallback: اگر همه چی فیل شد، ما فقط لینک مستقیم پیش‌نمایش رو میسازیم
        # مثلا: https://archive.ph/latest/ENC_URL
        # این روش غیرمطمئنه ولی شاید جواب بده
        return f"https://archive.is/latest/{original_url}"
        
    except Exception as e:
        write_log(f"Archive Error for {original_url}: {str(e)}")
        return None

def fetch_latest_articles():
    articles_to_archive = []
    
    for src in SOURCES:
        write_log(f"Processing {src['name']}...")
        try:
            if src.get("rss"):
                # روش RSS (مطمئن ترین روش برای ایران)
                feed = feedparser.parse(src["rss"])
                if feed.entries:
                    # اولین آیتم تازه رو برمیداریم
                    latest = feed.entries[0]
                    link = latest.link
                    title = latest.title
                    articles_to_archive.append({
                        "source": src["name"],
                        "title": title[:50],
                        "original": link
                    })
                    write_log(f"Found via RSS: {link}")
                else:
                    write_log(f"RSS Empty or Blocked for {src['name']}")
            
            else:
                # روش HTML Scraping برای New Yorker (چون RSS نداره)
                r = requests.get(src["url"], headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
                soup = BeautifulSoup(r.text, 'html.parser')
                # پیدا کردن اولین لینک مقاله
                link_elem = soup.select_one(src["selector"])
                if link_elem and link_elem.get("href"):
                    link = link_elem.get("href")
                    if not link.startswith("http"):
                        link = "https://www.newyorker.com" + link
                    articles_to_archive.append({
                        "source": src["name"],
                        "title": link_elem.text.strip()[:50],
                        "original": link
                    })
                    write_log(f"Found via HTML: {link}")
                else:
                    write_log(f"HTML Selector failed for {src['name']}")
                    
        except Exception as e:
            write_log(f"Fetch Error {src['name']}: {str(e)}")

    return articles_to_archive

def main():
    write_log("=== Job Started ===")
    
    # 1. مطالب جدید رو بگیر
    articles = fetch_latest_articles()
    
    if not articles:
        write_log("No articles found. Exiting.")
        return

    # 2. لینک archive هر کدوم رو بدست بیار
    output_lines = []
    output_lines.append(f"# Last Run: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    
    for art in articles:
        write_log(f"Archiving {art['original']}...")
        # یه تاخیر ۳ ثانیه‌ای که archive.ph ریت لیمیت نکنه
        time.sleep(3) 
        archive_url = get_archive_link(art['original'])
        
        if archive_url:
            line = f"[{art['source']}] {art['title']}\nOriginal: {art['original']}\nArchive: {archive_url}\n---\n"
            output_lines.append(line)
            write_log(f"Success: {archive_url}")
        else:
            output_lines.append(f"[{art['source']}] FAILED to archive: {art['original']}\n---\n")
            write_log(f"Failed to archive: {art['original']}")

    # 3. بنویس تو فایل
    os.makedirs(REPO_DIR, exist_ok=True)
    file_path = os.path.join(REPO_DIR, "latest_archives.txt")
    
    with open(file_path, "w", encoding="utf-8") as f:
        f.writelines(output_lines)
        
    write_log(f"File saved to {file_path}")
    write_log("=== Job Finished ===")

if __name__ == "__main__":
    main()
