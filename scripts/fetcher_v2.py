#!/usr/bin/env python3
"""
Fetch latest articles from Foreign Affairs, Foreign Policy, and New Yorker,
then retrieve their archive.is/ph/md snapshots.
Improved version with better rate-limit handling.
"""

import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import List, Dict, Optional
from urllib.parse import quote_plus, urlparse

import requests
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
TIMEOUT = 30
REQUEST_DELAY = 20  # افزایش از ۵ به ۲۰ ثانیه
INTER_DOMAIN_DELAY = 8  # تأخیر بین دامنه‌ها
MAX_ARTICLES_PER_RUN = 8  # محدودیت تعداد مقالات در هر اجرا
RETRY_COOLDOWN = 180  # ۳ دقیقه مکث بعد از 429

ARCHIVE_DOMAINS = ["archive.is", "archive.ph", "archive.md"]

SOURCES = {
    "Foreign Affairs": "https://www.foreignaffairs.com",
    "Foreign Policy": "https://foreignpolicy.com",
    "New Yorker": "https://www.newyorker.com",
}

OUTPUT_FILE = "articles/latest_archives.txt"
SEEN_FILE = "articles/seen_articles.txt"
LOG_FILE = "articles/scraper.log"

# ─────────────────────────────────────────────────────────────────────────────
# Domain status tracking with retry logic
# ─────────────────────────────────────────────────────────────────────────────
DOMAIN_STATUS = {
    domain: {
        "enabled": True,
        "reason": None,
        "retry_after": None,
        "fail_count": 0,
        "last_success": None,
    }
    for domain in ARCHIVE_DOMAINS
}

current_domain_index = 0  # برای round-robin


def write_log(message: str):
    """Write timestamped log message."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    log_line = f"[{timestamp}] {message}\n"
    print(log_line.rstrip())
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_line)


def disable_domain_temporarily(domain: str, reason: str, cooldown: int = RETRY_COOLDOWN):
    """Temporarily disable domain with retry capability."""
    st = DOMAIN_STATUS[domain]
    st["fail_count"] += 1
    
    # بعد از ۳ خطای متوالی، کاملاً غیرفعال کن
    if st["fail_count"] >= 3:
        st["enabled"] = False
        st["reason"] = f"{reason} (after {st['fail_count']} consecutive failures)"
        write_log(f"❌ PERMANENTLY disabled {domain}: {st['reason']}")
    else:
        st["retry_after"] = time.time() + cooldown
        write_log(
            f"⏸️  PAUSED {domain} for {cooldown}s (attempt {st['fail_count']}/3): {reason}"
        )


def can_use_domain(domain: str) -> bool:
    """Check if domain is available for use."""
    st = DOMAIN_STATUS[domain]
    
    if not st["enabled"]:
        return False
    
    # اگر در حال مکث است
    if st["retry_after"] and time.time() < st["retry_after"]:
        return False
    
    # اگر زمان مکث تمام شده، دوباره فعال کن
    if st["retry_after"] and time.time() >= st["retry_after"]:
        st["retry_after"] = None
        st["fail_count"] = max(0, st["fail_count"] - 1)  # کاهش تعداد خطا
        write_log(f"✅ Re-enabled {domain} after cooldown")
    
    return True


def mark_domain_success(domain: str):
    """Mark successful request to domain."""
    st = DOMAIN_STATUS[domain]
    st["last_success"] = time.time()
    st["fail_count"] = max(0, st["fail_count"] - 1)  # کاهش تدریجی fail count


def get_next_available_domain() -> Optional[str]:
    """Get next available domain using round-robin."""
    global current_domain_index
    
    for _ in range(len(ARCHIVE_DOMAINS)):
        domain = ARCHIVE_DOMAINS[current_domain_index]
        current_domain_index = (current_domain_index + 1) % len(ARCHIVE_DOMAINS)
        
        if can_use_domain(domain):
            return domain
    
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Utility functions
# ─────────────────────────────────────────────────────────────────────────────
def load_seen_articles() -> set:
    """Load previously seen article URLs."""
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())


def save_seen_articles(seen: set):
    """Save seen article URLs."""
    os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        for url in sorted(seen):
            f.write(url + "\n")


def fetch_page(url: str) -> Optional[str]:
    """Fetch page content with error handling."""
    try:
        headers = {"User-Agent": USER_AGENT}
        resp = requests.get(url, headers=headers, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        write_log(f"Error fetching {url}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Article extraction
# ─────────────────────────────────────────────────────────────────────────────
def extract_foreign_affairs_articles(html: str, base_url: str) -> List[Dict]:
    """Extract articles from Foreign Affairs."""
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    
    for link in soup.select("a[href*='/articles/']"):
        href = link.get("href", "")
        if not href or "/articles/" not in href:
            continue
        
        full_url = href if href.startswith("http") else base_url + href
        title = link.get_text(strip=True) or "Untitled"
        
        articles.append({"title": title, "url": full_url, "source": "Foreign Affairs"})
    
    return articles


def extract_foreign_policy_articles(html: str, base_url: str) -> List[Dict]:
    """Extract articles from Foreign Policy."""
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    
    for link in soup.select("a[href*='/20']"):
        href = link.get("href", "")
        if not href or not re.search(r"/\d{4}/\d{2}/\d{2}/", href):
            continue
        
        full_url = href if href.startswith("http") else base_url + href
        title = link.get_text(strip=True) or "Untitled"
        
        articles.append({"title": title, "url": full_url, "source": "Foreign Policy"})
    
    return articles


def extract_newyorker_articles(html: str, base_url: str) -> List[Dict]:
    """Extract articles from New Yorker."""
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    
    for link in soup.select("a[href*='/magazine/'], a[href*='/news/'], a[href*='/culture/']"):
        href = link.get("href", "")
        if not href:
            continue
        
        full_url = href if href.startswith("http") else base_url + href
        title = link.get_text(strip=True) or "Untitled"
        
        articles.append({"title": title, "url": full_url, "source": "New Yorker"})
    
    return articles


def scrape_latest_articles() -> List[Dict]:
    """Scrape latest articles from all sources."""
    all_articles = []
    
    for source_name, base_url in SOURCES.items():
        write_log(f"Scraping {source_name}...")
        html = fetch_page(base_url)
        
        if not html:
            write_log(f"Failed to fetch {source_name}")
            continue
        
        if source_name == "Foreign Affairs":
            articles = extract_foreign_affairs_articles(html, base_url)
        elif source_name == "Foreign Policy":
            articles = extract_foreign_policy_articles(html, base_url)
        elif source_name == "New Yorker":
            articles = extract_newyorker_articles(html, base_url)
        else:
            articles = []
        
        write_log(f"Found {len(articles)} articles from {source_name}")
        all_articles.extend(articles)
        
        time.sleep(3)  # مکث بین سایت‌ها
    
    return all_articles


# ─────────────────────────────────────────────────────────────────────────────
# Archive snapshot retrieval (IMPROVED)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_url_from_domain(domain: str, url: str, context: str) -> Optional[requests.Response]:
    """Fetch URL from archive domain with improved error handling."""
    if not can_use_domain(domain):
        return None
    
    try:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Connection": "keep-alive",
        }
        
        write_log(f"Requesting {domain} ({context})...")
        resp = requests.get(url, headers=headers, timeout=TIMEOUT, allow_redirects=True)
        
        if resp.status_code == 429:
            write_log(f"⚠️  Rate limited by {domain} during {context}")
            disable_domain_temporarily(domain, f"429 during {context}")
            return None
        
        if resp.status_code == 404:
            write_log(f"No snapshot found on {domain} (404)")
            return None
        
        resp.raise_for_status()
        mark_domain_success(domain)
        return resp
        
    except requests.exceptions.Timeout:
        write_log(f"Timeout on {domain} during {context}")
        return None
    except requests.exceptions.RequestException as e:
        write_log(f"Request error on {domain}: {e}")
        if "429" in str(e):
            disable_domain_temporarily(domain, f"429 in exception during {context}")
        return None
    except Exception as e:
        write_log(f"Unexpected error on {domain}: {e}")
        return None


def extract_snapshot_from_response(domain: str, resp: requests.Response, original_url: str) -> Optional[str]:
    """Extract snapshot URL from response."""
    final_url = resp.url
    
    # اگر به صفحه submit redirect شده، snapshot وجود ندارد
    if "/submit/" in final_url.lower():
        return None
    
    # اگر URL اصلی در final_url هست، snapshot پیدا شده
    parsed_original = urlparse(original_url)
    if parsed_original.netloc in final_url:
        write_log(f"✓ Found snapshot: {final_url}")
        return final_url
    
    return None


def get_latest_snapshot_from_domain(domain: str, original_url: str) -> Optional[str]:
    """
    Get latest snapshot from specific archive domain.
    ONLY uses direct lookup (no search page) to minimize requests.
    """
    if not can_use_domain(domain):
        return None
    
    # فقط direct lookup
    direct_url = f"https://{domain}/{original_url}"
    resp = fetch_url_from_domain(domain, direct_url, "direct lookup")
    
    if resp:
        snapshot = extract_snapshot_from_response(domain, resp, original_url)
        if snapshot:
            return snapshot
    
    return None


def get_archive_snapshot(original_url: str) -> Optional[str]:
    """
    Try to get archive snapshot using round-robin domain selection.
    """
    write_log(f"\n{'='*60}")
    write_log(f"Looking for snapshot of: {original_url}")
    
    # سعی در استفاده از دامنه‌های مختلف
    tried_domains = []
    
    for attempt in range(len(ARCHIVE_DOMAINS)):
        domain = get_next_available_domain()
        
        if not domain:
            write_log("⚠️  No available domains at the moment")
            break
        
        if domain in tried_domains:
            continue
        
        tried_domains.append(domain)
        write_log(f"Trying {domain}...")
        
        snapshot = get_latest_snapshot_from_domain(domain, original_url)
        
        if snapshot:
            write_log(f"✅ SUCCESS via {domain}")
            return snapshot
        
        # مکث بین دامنه‌ها
        if attempt < len(ARCHIVE_DOMAINS) - 1:
            write_log(f"Waiting {INTER_DOMAIN_DELAY}s before trying next domain...")
            time.sleep(INTER_DOMAIN_DELAY)
    
    write_log(f"❌ FAILED: No snapshot found (tried {len(tried_domains)} domains)")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Main workflow
# ─────────────────────────────────────────────────────────────────────────────
def main():
    write_log("="*80)
    write_log("Starting archive snapshot retrieval (IMPROVED VERSION)")
    write_log("="*80)
    
    # Load seen articles
    seen = load_seen_articles()
    write_log(f"Loaded {len(seen)} previously seen articles")
    
    # Scrape latest articles
    all_articles = scrape_latest_articles()
    write_log(f"\nTotal articles found: {len(all_articles)}")
    
    # Filter new articles
    new_articles = [a for a in all_articles if a["url"] not in seen]
    write_log(f"New articles to process: {len(new_articles)}")
    
    if not new_articles:
        write_log("No new articles found. Exiting.")
        return
    
    # محدود کردن تعداد مقالات
    if len(new_articles) > MAX_ARTICLES_PER_RUN:
        write_log(f"⚠️  Limiting to {MAX_ARTICLES_PER_RUN} articles per run (found {len(new_articles)})")
        new_articles = new_articles[:MAX_ARTICLES_PER_RUN]
    
    # Process articles
    archived = []
    
    for idx, article in enumerate(new_articles, 1):
        write_log(f"\n{'─'*80}")
        write_log(f"Processing article {idx}/{len(new_articles)}")
        write_log(f"Title: {article['title']}")
        write_log(f"Source: {article['source']}")
        
        snapshot_url = get_archive_snapshot(article["url"])
        
        if snapshot_url:
            archived.append({
                "title": article["title"],
                "source": article["source"],
                "original_url": article["url"],
                "snapshot_url": snapshot_url,
            })
        
        # Mark as seen
        seen.add(article["url"])
        
        # مکث بین مقالات
        if idx < len(new_articles):
            write_log(f"\nWaiting {REQUEST_DELAY}s before next article...")
            time.sleep(REQUEST_DELAY)
    
    # Save results
    save_seen_articles(seen)
    write_output(archived)
    
    write_log("\n" + "="*80)
    write_log(f"SUMMARY: Processed {len(new_articles)} articles, archived {len(archived)}")
    write_log("="*80)


def write_output(archived: List[Dict]):
    """Write archived articles to output file."""
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("# Latest Archived Articles\n")
        f.write(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n")
        
        if not archived:
            f.write("No articles archived in this run.\n")
            write_log(f"Output written with 0 entries")
            return
        
        for item in archived:
            f.write(f"## {item['title']}\n")
            f.write(f"**Source:** {item['source']}\n")
            f.write(f"**Original:** {item['original_url']}\n")
            f.write(f"**Archive:** {item['snapshot_url']}\n\n")
            f.write("---\n\n")
    
    write_log(f"✅ Output written with {len(archived)} entries to {OUTPUT_FILE}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        write_log("\n⚠️  Interrupted by user")
        sys.exit(1)
    except Exception as e:
        write_log(f"\n❌ Fatal error: {e}")
        import traceback
        write_log(traceback.format_exc())
        sys.exit(1)
