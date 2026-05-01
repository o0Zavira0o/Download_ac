from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Set
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from zoneinfo import ZoneInfo


# -----------------------
# تنظیمات کلی
# -----------------------

# بلاگ‌هایی که باید چک شوند
BLOGS = [
    # فقط در صورت نیاز URLها را ویرایش کن
    # حتما https را استفاده کن
    # (اگر سایت روی http است، همان را بزن)
    # مثال:
    # BlogConfig(name="yoyoloit", url="https://zavat.pw/blogs/yoyoloit"),
]

# چون نمی‌توانم به سایت وصل شوم، اینجا پرش را به‌صورت استاتیک می‌گذارم.
# تو اگر لازم شد فقط http/https را اصلاح کن.
BLOGS = [
    # اگر سایت روی http است، اینها را به http تغییر بده:
    # مثلا "http://zavat.pw/blogs/yoyoloit"
    # در غیر این صورت https استفاده کن.
    # من پیش‌فرض را https گذاشتم:
    # اگر جواب نداد، فقط https را به http عوض کن.
    # ===============================
    # این ۴ خط را مطابق خواسته‌ات استفاده کن:
    # ===============================
    # BlogConfig("yoyoloit", "http://zavat.pw/blogs/yoyoloit"),
    # BlogConfig("IrGens", "http://zavat.pw/blogs/IrGens"),
    # BlogConfig("AvaxGenius", "http://zavat.pw/blogs/AvaxGenius"),
    # BlogConfig("hill0", "http://zavat.pw/blogs/hill0"),
]

# برای اینکه بالا واقعا کار کند باید BlogConfig را تعریف کنیم:
@dataclass(frozen=True)
class BlogConfig:
    name: str
    url: str


BLOGS = [
    BlogConfig("yoyoloit", "http://zavat.pw/blogs/yoyoloit"),
    BlogConfig("IrGens", "http://zavat.pw/blogs/IrGens"),
    BlogConfig("AvaxGenius", "http://zavat.pw/blogs/IrGens".replace("IrGens", "AvaxGenius")),  # اصلاح ساده
    BlogConfig("hill0", "http://zavat.pw/blogs/hill0"),
]


# منطقه زمانی: تهران
try:
    TZ = ZoneInfo("Asia/Tehran")
except Exception:
    TZ = ZoneInfo("UTC")

# مسیرهای فایل‌ها (نسبت به ریشه ریپو)
ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "zavat_data"
LOGS_DIR = DATA_DIR / "logs"
STATE_FILE = DATA_DIR / "state.json"

USER_AGENT = (
    "Mozilla/5.0 (compatible; ZavatWatcher/1.0; +https://github.com/your-username/your-repo)"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
LOGGER = logging.getLogger("zavat_watcher")


# -----------------------
# مدل داده
# -----------------------

@dataclass
class PostInfo:
    blog: str
    title: str
    details: str
    url: str


# -----------------------
# مدیریت state
# -----------------------

def ensure_dirs() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_state() -> Dict[str, Any]:
    if not STATE_FILE.exists():
        return {"seen_urls": []}
    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        LOGGER.warning("Could not read state file, starting fresh: %s", exc)
        return {"seen_urls": []}


def save_state(state: Dict[str, Any]) -> None:
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# -----------------------
# HTTP و پارس HTML
# -----------------------

def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def fetch_soup(session: requests.Session, url: str) -> BeautifulSoup | None:
    try:
        resp = session.get(url, timeout=25)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:
        LOGGER.error("Error fetching %s: %s", url, exc)
        return None


def extract_posts_from_blog(
    soup: BeautifulSoup,
    blog: BlogConfig,
) -> List[PostInfo]:
    """
    تلاش می‌کند همه پست‌های موجود در صفحه بلاگ را پیدا کند.
    از روی HTML واقعی ممکن است لازم باشد این تابع را کم‌وزیاد کنی.
    """

    posts: List[PostInfo] = []
    seen_urls: Set[str] = set()

    base_netloc = urlparse(blog.url).netloc
    blog_path = urlparse(blog.url).path.rstrip("/")

    # سناریو ۱: ساختار شبیه WordPress، پست‌ها داخل <article>
    for article in soup.select("article"):
        link = article.find("a", href=True)
        if not link:
            continue
        href = link["href"]
        full_url = urljoin(blog.url, href)

        # فقط لینک‌هایی که روی همان دامنه هستند
        if urlparse(full_url).netloc != base_netloc:
            continue

        # خود صفحه بلاگ را رد کن
        if urlparse(full_url).path.rstrip("/") == blog_path:
            continue

        title = link.get_text(strip=True)
        if not title:
            continue

        if full_url in seen_urls:
            continue

        seen_urls.add(full_url)
        posts.append(PostInfo(blog=blog.name, title=title, details="", url=full_url))

    # اگر چیزی پیدا نشد، fallback عمومی روی همه <a>ها
    if not posts:
        for link in soup.find_all("a", href=True):
            href = link["href"]
            if not href or href.startswith("#"):
                continue
            full_url = urljoin(blog.url, href)
            parsed = urlparse(full_url)

            # فقط همان دامنه
            if parsed.netloc != base_netloc:
                continue

            # خود بلاگ یا صفحه‌های pagination/تگ/کَتگوری را رد کن تا حد امکان
            path = parsed.path.rstrip("/")
            if path == blog_path:
                continue
            if any(x in path.lower() for x in ["tag", "category", "page", "search"]):
                continue

            title = link.get_text(strip=True)
            if not title:
                continue

            if full_url in seen_urls:
                continue

            seen_urls.add(full_url)
            posts.append(PostInfo(blog=blog.name, title=title, details="", url=full_url))

    return posts


def extract_details_from_post(soup: BeautifulSoup) -> str:
    """
    تلاش می‌کند از داخل متن پست، چیزهایی شبیه «فرمت»، «حجم»، «MB», «EPUB», «PDF» را پیدا کند
    و چند خط مرتبط را کنار هم به‌عنوان جزئیات برگرداند.
    """

    keywords = [
        "فرمت", "حجم", "format", "size",
        "mb", "gb", "kb",
        "مگابایت", "گیگابایت", "کیلوبایت",
        "pdf", "epub", "mobi",
    ]

    candidates: List[str] = []
    for text in soup.stripped_strings:
        t = text.strip()
        if not t:
            continue
        low = t.lower()
        if any(k in low for k in keywords):
            # از تکرار جلوگیری کنیم
            if t not in candidates:
                candidates.append(t)

        if len(candidates) >= 5:
            break

    if not candidates:
        return "—"

    # اگر متن خیلی طولانی بود، کمی کوتاهش کنیم
    cleaned: List[str] = []
    for c in candidates:
        c = " ".join(c.split())
        if len(c) > 200:
            c = c[:200] + "..."
        cleaned.append(c)

    return " | ".join(cleaned)


# -----------------------
# نوشتن در فایل لاگ روزانه
# -----------------------

def append_posts_to_daily_log(posts: List[PostInfo]) -> None:
    if not posts:
        return

    now = datetime.now(TZ)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")
    log_path = LOGS_DIR / f"{date_str}.txt"

    with log_path.open("a", encoding="utf-8") as f:
        for post in posts:
            f.write("=" * 80 + "\n")
            f.write(f"{date_str} {time_str} ({TZ.key}) | {post.blog}\n")
            f.write(f"Title  : {post.title}\n")
            f.write(f"Details: {post.details}\n")
            f.write(f"Link   : {post.url}\n\n")


# -----------------------
# حلقه اصلی
# -----------------------

def main() -> None:
    ensure_dirs()
    state = load_state()
    seen_urls: Set[str] = set(state.get("seen_urls", []))

    session = make_session()
    new_posts: List[PostInfo] = []

    for blog in BLOGS:
        LOGGER.info("Checking blog: %s (%s)", blog.name, blog.url)
        soup = fetch_soup(session, blog.url)
        if soup is None:
            continue

        posts = extract_posts_from_blog(soup, blog)

        # فقط پست‌هایی که قبلا ندیده‌ایم
        for post in posts:
            if post.url in seen_urls:
                continue

            LOGGER.info("New post detected: %s", post.url)
            # برای هر پست جدید، صفحه خودش را هم باز می‌کنیم تا جزئیات فرمت/حجم را درآوریم
            post_soup = fetch_soup(session, post.url)
            if post_soup is not None:
                post.details = extract_details_from_post(post_soup)
            else:
                post.details = "—"

            new_posts.append(post)
            seen_urls.add(post.url)

    if not new_posts:
        LOGGER.info("No new posts found.")
    else:
        LOGGER.info("Found %d new posts.", len(new_posts))
        append_posts_to_daily_log(new_posts)

    # state را ذخیره کن
    state["seen_urls"] = sorted(seen_urls)
    save_state(state)


if __name__ == "__main__":
    main()
