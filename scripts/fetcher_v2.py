#!/usr/bin/env python3
"""
Article Scraper - Foreign Affairs, Foreign Policy, New Yorker
Extracts latest articles (48h) with deduplication
"""

import os
import sys
import re
import time
import traceback
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Set
from urllib.parse import urljoin, urlparse

try:
    import requests
    from bs4 import BeautifulSoup
    from dateutil import parser as date_parser
except ImportError as e:
    print(f"ERROR: Missing required package: {e}")
    print("Install with: pip install requests beautifulsoup4 python-dateutil")
    sys.exit(1)

# ═══════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
TIMEOUT = 30
REQUEST_DELAY = 10
MAX_AGE_HOURS = 48

# Output directory
OUTPUT_DIR = "articles"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "latest_articles.txt")
CACHE_FILE = os.path.join(OUTPUT_DIR, "seen_urls.txt")
LOG_FILE = os.path.join(OUTPUT_DIR, "scraper.log")

SOURCES = {
    "Foreign Affairs": "https://www.foreignaffairs.com/most-recent",
    "Foreign Policy": "https://foreignpolicy.com/the-magazine/",
    "New Yorker": "https://www.newyorker.com/magazine",
}


# ═══════════════════════════════════════════════════════════════════════════
# Logging
# ═══════════════════════════════════════════════════════════════════════════
def ensure_output_dir():
    """Create output directory if it doesn't exist."""
    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        return True
    except Exception as e:
        print(f"FATAL: Cannot create output directory: {e}")
        return False


def write_log(message: str, level: str = "INFO"):
    """Write log message to both console and file."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{timestamp}] [{level}] {message}"
    
    # Always print to console
    print(log_line)
    
    # Try to write to file
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_line + "\n")
    except Exception as e:
        print(f"WARNING: Could not write to log file: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# HTTP Utilities
# ═══════════════════════════════════════════════════════════════════════════
def fetch_page(url: str, retries: int = 3) -> Optional[str]:
    """Fetch page content with retry logic."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    
    for attempt in range(retries):
        try:
            write_log(f"Fetching {url} (attempt {attempt + 1}/{retries})")
            resp = requests.get(
                url,
                headers=headers,
                timeout=TIMEOUT,
                allow_redirects=True
            )
            resp.raise_for_status()
            write_log(f"✓ Successfully fetched {url} ({len(resp.text)} bytes)")
            return resp.text
            
        except requests.exceptions.Timeout:
            write_log(f"Timeout fetching {url}", "WARNING")
            if attempt < retries - 1:
                time.sleep(5)
                
        except requests.exceptions.HTTPError as e:
            write_log(f"HTTP error {e.response.status_code} for {url}", "ERROR")
            return None
            
        except Exception as e:
            write_log(f"Error fetching {url}: {type(e).__name__}: {e}", "ERROR")
            if attempt < retries - 1:
                time.sleep(5)
    
    write_log(f"Failed to fetch {url} after {retries} attempts", "ERROR")
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Date Parsing
# ═══════════════════════════════════════════════════════════════════════════
def parse_date(date_str: str) -> Optional[datetime]:
    """Parse date string to datetime object."""
    if not date_str:
        return None
    
    try:
        # Try ISO format first
        dt = date_parser.parse(date_str)
        # Ensure timezone aware
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def extract_date_from_url(url: str) -> Optional[datetime]:
    """Extract date from URL pattern like /2025/04/29/."""
    match = re.search(r'/(\d{4})/(\d{2})/(\d{2})/', url)
    if match:
        try:
            return datetime(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
                tzinfo=timezone.utc
            )
        except:
            pass
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Scrapers
# ═══════════════════════════════════════════════════════════════════════════
def scrape_foreign_affairs(url: str) -> List[Dict]:
    """Scrape Foreign Affairs."""
    write_log("Scraping Foreign Affairs...")
    html = fetch_page(url)
    if not html:
        return []
    
    try:
        soup = BeautifulSoup(html, "html.parser")
        articles = []
        
        # Try multiple selectors
        selectors = [
            "article",
            "div.article-card",
            "div.promo-block",
            "div[class*='article']",
            "div[class*='story']",
        ]
        
        found_elements = []
        for selector in selectors:
            found_elements.extend(soup.select(selector))
        
        write_log(f"Found {len(found_elements)} potential article elements")
        
        for element in found_elements:
            # Find link
            link = element.find("a", href=re.compile(r'/(articles?|20\d{2})'))
            if not link:
                continue
            
            href = link.get("href", "")
            if not href:
                continue
            
            full_url = urljoin(url, href)
            
            # Validate URL
            if "foreignaffairs.com" not in full_url:
                continue
            
            # Find title
            title = None
            for tag in element.find_all(["h1", "h2", "h3", "h4", "h5"]):
                text = tag.get_text(strip=True)
                if len(text) > 15:
                    title = text
                    break
            
            if not title:
                title = link.get_text(strip=True)
            
            if not title or len(title) < 10:
                continue
            
            # Find date
            pub_date = None
            time_tag = element.find("time")
            if time_tag:
                date_str = time_tag.get("datetime") or time_tag.get_text(strip=True)
                pub_date = parse_date(date_str)
            
            if not pub_date:
                pub_date = extract_date_from_url(full_url)
            
            if not pub_date:
                pub_date = datetime.now(timezone.utc)
            
            articles.append({
                "title": title[:200],  # Limit title length
                "url": full_url,
                "source": "Foreign Affairs",
                "published": pub_date,
            })
        
        # Remove duplicates by URL
        seen = set()
        unique = []
        for art in articles:
            if art["url"] not in seen:
                seen.add(art["url"])
                unique.append(art)
        
        write_log(f"✓ Extracted {len(unique)} unique articles from Foreign Affairs")
        return unique
        
    except Exception as e:
        write_log(f"Error parsing Foreign Affairs: {e}", "ERROR")
        write_log(traceback.format_exc(), "DEBUG")
        return []


def scrape_foreign_policy(url: str) -> List[Dict]:
    """Scrape Foreign Policy."""
    write_log("Scraping Foreign Policy...")
    html = fetch_page(url)
    if not html:
        return []
    
    try:
        soup = BeautifulSoup(html, "html.parser")
        articles = []
        
        selectors = [
            "article",
            "div.article-card",
            "div.story-card",
            "div[class*='content']",
            "div[class*='story']",
        ]
        
        found_elements = []
        for selector in selectors:
            found_elements.extend(soup.select(selector))
        
        write_log(f"Found {len(found_elements)} potential article elements")
        
        for element in found_elements:
            link = element.find("a", href=re.compile(r'/20\d{2}/'))
            if not link:
                continue
            
            href = link.get("href", "")
            if not href:
                continue
            
            full_url = urljoin(url, href)
            
            if "foreignpolicy.com" not in full_url:
                continue
            
            title = None
            for tag in element.find_all(["h1", "h2", "h3", "h4", "h5"]):
                text = tag.get_text(strip=True)
                if len(text) > 15:
                    title = text
                    break
            
            if not title:
                title = link.get_text(strip=True)
            
            if not title or len(title) < 10:
                continue
            
            pub_date = extract_date_from_url(full_url)
            
            if not pub_date:
                time_tag = element.find("time")
                if time_tag:
                    date_str = time_tag.get("datetime") or time_tag.get_text(strip=True)
                    pub_date = parse_date(date_str)
            
            if not pub_date:
                pub_date = datetime.now(timezone.utc)
            
            articles.append({
                "title": title[:200],
                "url": full_url,
                "source": "Foreign Policy",
                "published": pub_date,
            })
        
        seen = set()
        unique = []
        for art in articles:
            if art["url"] not in seen:
                seen.add(art["url"])
                unique.append(art)
        
        write_log(f"✓ Extracted {len(unique)} unique articles from Foreign Policy")
        return unique
        
    except Exception as e:
        write_log(f"Error parsing Foreign Policy: {e}", "ERROR")
        write_log(traceback.format_exc(), "DEBUG")
        return []


def scrape_new_yorker(url: str) -> List[Dict]:
    """Scrape New Yorker."""
    write_log("Scraping New Yorker...")
    html = fetch_page(url)
    if not html:
        return []
    
    try:
        soup = BeautifulSoup(html, "html.parser")
        articles = []
        
        selectors = [
            "article",
            "div[class*='River']",
            "div[class*='Summary']",
            "div[class*='story']",
            "div[class*='item']",
        ]
        
        found_elements = []
        for selector in selectors:
            found_elements.extend(soup.select(selector))
        
        write_log(f"Found {len(found_elements)} potential article elements")
        
        for element in found_elements:
            link = element.find("a", href=re.compile(r'/(magazine|news|culture|books)/'))
            if not link:
                continue
            
            href = link.get("href", "")
            if not href:
                continue
            
            full_url = urljoin(url, href)
            
            if "newyorker.com" not in full_url:
                continue
            
            title = None
            for tag in element.find_all(["h1", "h2", "h3", "h4", "h5"]):
                text = tag.get_text(strip=True)
                if len(text) > 15:
                    title = text
                    break
            
            if not title:
                title = link.get_text(strip=True)
            
            if not title or len(title) < 10:
                continue
            
            pub_date = None
            time_tag = element.find("time")
            if time_tag:
                date_str = time_tag.get("datetime") or time_tag.get_text(strip=True)
                pub_date = parse_date(date_str)
            
            if not pub_date:
                pub_date = extract_date_from_url(full_url)
            
            if not pub_date:
                pub_date = datetime.now(timezone.utc)
            
            articles.append({
                "title": title[:200],
                "url": full_url,
                "source": "New Yorker",
                "published": pub_date,
            })
        
        seen = set()
        unique = []
        for art in articles:
            if art["url"] not in seen:
                seen.add(art["url"])
                unique.append(art)
        
        write_log(f"✓ Extracted {len(unique)} unique articles from New Yorker")
        return unique
        
    except Exception as e:
        write_log(f"Error parsing New Yorker: {e}", "ERROR")
        write_log(traceback.format_exc(), "DEBUG")
        return []


# ═══════════════════════════════════════════════════════════════════════════
# Cache Management
# ═══════════════════════════════════════════════════════════════════════════
def load_seen_urls() -> Set[str]:
    """Load seen URLs from cache."""
    if not os.path.exists(CACHE_FILE):
        write_log("No cache file found, starting fresh")
        return set()
    
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            urls = set(line.strip() for line in f if line.strip())
        write_log(f"Loaded {len(urls)} URLs from cache")
        return urls
    except Exception as e:
        write_log(f"Error loading cache: {e}", "WARNING")
        return set()


def save_seen_urls(urls: Set[str]):
    """Save seen URLs to cache."""
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            for url in sorted(urls):
                f.write(url + "\n")
        write_log(f"Saved {len(urls)} URLs to cache")
    except Exception as e:
        write_log(f"Error saving cache: {e}", "ERROR")


# ═══════════════════════════════════════════════════════════════════════════
# Filtering & Output
# ═══════════════════════════════════════════════════════════════════════════
def filter_recent(articles: List[Dict]) -> List[Dict]:
    """Keep only articles from last MAX_AGE_HOURS."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
    recent = [a for a in articles if a["published"] >= cutoff]
    write_log(f"Filtered to {len(recent)} articles within {MAX_AGE_HOURS}h window")
    return recent


def write_output(articles: List[Dict]):
    """Write articles to output file."""
    try:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write(f"Latest Articles (Last {MAX_AGE_HOURS} hours)\n")
            f.write(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
            f.write(f"Total: {len(articles)} articles\n")
            f.write("=" * 80 + "\n\n")
            
            for article in articles:
                pub_time = article["published"].strftime("%Y-%m-%d %H:%M UTC")
                f.write(f"Source: {article['source']}\n")
                f.write(f"Title: {article['title']}\n")
                f.write(f"URL: {article['url']}\n")
                f.write(f"Published: {pub_time}\n")
                f.write("-" * 80 + "\n\n")
        
        write_log(f"✓ Wrote {len(articles)} articles to {OUTPUT_FILE}")
    except Exception as e:
        write_log(f"Error writing output: {e}", "ERROR")


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════
def main():
    """Main execution."""
    write_log("=" * 80)
    write_log("Article Scraper Starting")
    write_log("=" * 80)
    
    # Ensure output directory exists
    if not ensure_output_dir():
        sys.exit(1)
    
    # Load cache
    seen_urls = load_seen_urls()
    
    # Scrape all sources
    all_articles = []
    
    try:
        all_articles.extend(scrape_foreign_affairs(SOURCES["Foreign Affairs"]))
        time.sleep(REQUEST_DELAY)
        
        all_articles.extend(scrape_foreign_policy(SOURCES["Foreign Policy"]))
        time.sleep(REQUEST_DELAY)
        
        all_articles.extend(scrape_new_yorker(SOURCES["New Yorker"]))
        
    except KeyboardInterrupt:
        write_log("Interrupted by user", "WARNING")
        sys.exit(1)
    except Exception as e:
        write_log(f"Unexpected error during scraping: {e}", "ERROR")
        write_log(traceback.format_exc(), "DEBUG")
    
    write_log(f"Total articles collected: {len(all_articles)}")
    
    # Filter new articles
    new_articles = []
    for article in all_articles:
        if article["url"] not in seen_urls:
            new_articles.append(article)
            seen_urls.add(article["url"])
    
    write_log(f"New articles (not in cache): {len(new_articles)}")
    
    # Filter by date
    recent_articles = filter_recent(new_articles)
    
    # Sort by date (newest first)
    recent_articles.sort(key=lambda x: x["published"], reverse=True)
    
    # Write output
    write_output(recent_articles)
    
    # Save cache
    save_seen_urls(seen_urls)
    
    write_log("=" * 80)
    write_log(f"✓ Scraping completed: {len(recent_articles)} new recent articles")
    write_log("=" * 80)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FATAL ERROR: {e}")
        print(traceback.format_exc())
        sys.exit(1)
