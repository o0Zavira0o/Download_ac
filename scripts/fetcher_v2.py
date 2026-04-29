#!/usr/bin/env python3
"""
Scrape latest articles from Foreign Affairs, Foreign Policy, and New Yorker.
Only saves direct links with titles, sorted by publish time.
Removes duplicates and articles older than 48 hours.
"""

import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
TIMEOUT = 30
REQUEST_DELAY = 10
MAX_AGE_HOURS = 48

SOURCES = {
    "Foreign Affairs": "https://www.foreignaffairs.com/most-recent",
    "Foreign Policy": "https://foreignpolicy.com/the-magazine/",
    "New Yorker": "https://www.newyorker.com/magazine",
}

OUTPUT_FILE = "articles/latest_articles.txt"
CACHE_FILE = "articles/seen_urls.txt"
LOG_FILE = "articles/scraper.log"


def write_log(message: str):
    """Write timestamped log message."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    log_line = f"[{timestamp}] {message}"
    print(log_line)
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_line + "\n")


def fetch_page(url: str) -> Optional[str]:
    """Fetch page content with proper headers."""
    try:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        resp = requests.get(url, headers=headers, timeout=TIMEOUT, allow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        write_log(f"❌ Error fetching {url}: {e}")
        return None


def extract_date_from_text(text: str) -> Optional[datetime]:
    """Try to extract date from text."""
    try:
        return date_parser.parse(text, fuzzy=True)
    except:
        return None


def scrape_foreign_affairs(url: str) -> List[Dict]:
    """Scrape Foreign Affairs most recent page."""
    write_log(f"📰 Scraping Foreign Affairs...")
    html = fetch_page(url)
    if not html:
        return []
    
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    
    for article in soup.select("article, div.article-card, div.promo-block"):
        link_tag = article.select_one("a[href*='/articles/'], a[href*='/20']")
        if not link_tag:
            continue
        
        href = link_tag.get("href", "")
        if not href:
            continue
        
        full_url = urljoin(url, href)
        
        title_tag = article.select_one("h2, h3, h4, .article-title, .promo-title")
        title = title_tag.get_text(strip=True) if title_tag else link_tag.get_text(strip=True)
        
        if not title or len(title) < 10:
            continue
        
        date_tag = article.select_one("time, .date, .publish-date, .article-date")
        pub_date = None
        if date_tag:
            date_str = date_tag.get("datetime") or date_tag.get_text(strip=True)
            pub_date = extract_date_from_text(date_str)
        
        articles.append({
            "title": title,
            "url": full_url,
            "source": "Foreign Affairs",
            "published": pub_date or datetime.now(timezone.utc),
        })
    
    write_log(f"✓ Found {len(articles)} articles from Foreign Affairs")
    return articles


def scrape_foreign_policy(url: str) -> List[Dict]:
    """Scrape Foreign Policy magazine page."""
    write_log(f"📰 Scraping Foreign Policy...")
    html = fetch_page(url)
    if not html:
        return []
    
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    
    for article in soup.select("article, div.article-card, div.story-card, div.content-card"):
        link_tag = article.select_one("a[href*='/20']")
        if not link_tag:
            continue
        
        href = link_tag.get("href", "")
        if not href:
            continue
        
        full_url = urljoin(url, href)
        
        title_tag = article.select_one("h2, h3, h4, .hed, .title")
        title = title_tag.get_text(strip=True) if title_tag else link_tag.get_text(strip=True)
        
        if not title or len(title) < 10:
            continue
        
        date_match = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", href)
        pub_date = None
        if date_match:
            try:
                pub_date = datetime(
                    int(date_match.group(1)),
                    int(date_match.group(2)),
                    int(date_match.group(3)),
                    tzinfo=timezone.utc
                )
            except:
                pass
        
        if not pub_date:
            date_tag = article.select_one("time, .date, .publish-date")
            if date_tag:
                date_str = date_tag.get("datetime") or date_tag.get_text(strip=True)
                pub_date = extract_date_from_text(date_str)
        
        articles.append({
            "title": title,
            "url": full_url,
            "source": "Foreign Policy",
            "published": pub_date or datetime.now(timezone.utc),
        })
    
    write_log(f"✓ Found {len(articles)} articles from Foreign Policy")
    return articles


def scrape_new_yorker(url: str) -> List[Dict]:
    """Scrape New Yorker magazine page."""
    write_log(f"📰 Scraping New Yorker...")
    html = fetch_page(url)
    if not html:
        return []
    
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    
    for article in soup.select("article, div.River__riverItem, div.SummaryItemWrapper"):
        link_tag = article.select_one("a[href*='/magazine/'], a[href*='/news/'], a[href*='/culture/']")
        if not link_tag:
            continue
        
        href = link_tag.get("href", "")
        if not href:
            continue
        
        full_url = urljoin(url, href)
        
        title_tag = article.select_one("h2, h3, h4, .SummaryItemHed, .River__hed")
        title = title_tag.get_text(strip=True) if title_tag else link_tag.get_text(strip=True)
        
        if not title or len(title) < 10:
            continue
        
        date_tag = article.select_one("time, .SummaryItemPublishDate, .River__publishDate")
        pub_date = None
        if date_tag:
            date_str = date_tag.get("datetime") or date_tag.get_text(strip=True)
            pub_date = extract_date_from_text(date_str)
        
        articles.append({
            "title": title,
            "url": full_url,
            "source": "New Yorker",
            "published": pub_date or datetime.now(timezone.utc),
        })
    
    write_log(f"✓ Found {len(articles)} articles from New Yorker")
    return articles


def load_seen_urls() -> set:
    """Load previously seen URLs from cache."""
    if not os.path.exists(CACHE_FILE):
        return set()
    
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())


def save_seen_urls(urls: set):
    """Save seen URLs to cache."""
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        for url in sorted(urls):
            f.write(url + "\n")


def filter_recent_articles(articles: List[Dict]) -> List[Dict]:
    """Filter articles to only include those from last 48 hours."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
    recent = []
    
    for article in articles:
        pub_date = article.get("published")
        if pub_date and pub_date >= cutoff:
            recent.append(article)
    
    return recent


def write_output(articles: List[Dict]):
    """Write articles to output file."""
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(f"Latest Articles (Last {MAX_AGE_HOURS} hours)\n")
        f.write(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
        f.write("=" * 80 + "\n\n")
        
        for article in articles:
            pub_time = article["published"].strftime("%Y-%m-%d %H:%M UTC")
            f.write(f"Source: {article['source']}\n")
            f.write(f"Title: {article['title']}\n")
            f.write(f"URL: {article['url']}\n")
            f.write(f"Published: {pub_time}\n")
            f.write("-" * 80 + "\n\n")


def main():
    """Main execution function."""
    write_log("🚀 Starting article scraper...")
    
    seen_urls = load_seen_urls()
    all_articles = []
    
    # Scrape each source
    all_articles.extend(scrape_foreign_affairs(SOURCES["Foreign Affairs"]))
    time.sleep(REQUEST_DELAY)
    
    all_articles.extend(scrape_foreign_policy(SOURCES["Foreign Policy"]))
    time.sleep(REQUEST_DELAY)
    
    all_articles.extend(scrape_new_yorker(SOURCES["New Yorker"]))
    
    write_log(f"📊 Total articles found: {len(all_articles)}")
    
    # Filter duplicates
    new_articles = []
    for article in all_articles:
        if article["url"] not in seen_urls:
            new_articles.append(article)
            seen_urls.add(article["url"])
    
    write_log(f"🆕 New articles (not seen before): {len(new_articles)}")
    
    # Filter by date
    recent_articles = filter_recent_articles(new_articles)
    write_log(f"⏰ Recent articles (last {MAX_AGE_HOURS}h): {len(recent_articles)}")
    
    # Sort by publish date (newest first)
    recent_articles.sort(key=lambda x: x["published"], reverse=True)
    
    # Write output
    write_output(recent_articles)
    write_log(f"💾 Saved {len(recent_articles)} articles to {OUTPUT_FILE}")
    
    # Update cache
    save_seen_urls(seen_urls)
    write_log(f"✅ Updated cache with {len(seen_urls)} total URLs")
    
    write_log("✨ Scraping completed successfully!")


if __name__ == "__main__":
    main()
