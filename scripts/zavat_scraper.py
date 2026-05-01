from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Set, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from zoneinfo import ZoneInfo


# -----------------------
# تنظیمات کلی
# -----------------------

@dataclass(frozen=True)
class BlogConfig:
    name: str
    url: str


# اگر سایت روی https است، http را به https تغییر بده
BLOGS: List[BlogConfig] = [
    BlogConfig("yoyoloit",   "http://zavat.pw/blogs/yoyoloit"),
    BlogConfig("IrGens",     "http://zavat.pw/blogs/IrGens"),
    BlogConfig("AvaxGenius", "http://zavat.pw/blogs/AvaxGenius"),
    BlogConfig("hill0",      "http://zavat.pw/blogs/hill0"),
]

# آدرس‌هایی که «صفحه پست» محسوب می‌شوند
CONTENT_PREFIXES = [
    "/ebooks/",
    "/magazines/",
    "/comics/",
    "/newspapers/",
    "/music/",
    "/audiobooks/",
    "/software/",
    "/games/",
    "/graphics/",
    "/girls/",
    "/hraphile/",
    "/tvseries/",
    "/anime/",
    "/video/",
]

# الگوهایی که اگر در آدرس تصویر باشند، یعنی احتمالا بنر/حمایت/لوگو هستند
UNWANTED_IMAGE_SUBSTRINGS = [
    "donate",
    "donation",
    "bitcoin",
    "support",
    "banner",
    "ads",
    "logo",
]

# منطقه زمانی لاگ‌ها
try:
    TZ = ZoneInfo("Asia/Tehran")
except Exception:
    TZ = ZoneInfo("UTC")

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "zavat_data"
LOGS_DIR = DATA_DIR / "logs"
IMAGES_DIR = DATA_DIR / "images"
MD_DIR = DATA_DIR / "md"
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
    image_rel_path: Optional[str] = None  # مسیر نسبی تصویر برای markdown


# -----------------------
# مدیریت دایرکتوری و state
# -----------------------

def ensure_dirs() -> None:
    for d in (LOGS_DIR, IMAGES_DIR, MD_DIR):
        d.mkdir(parents=True, exist_ok=True)


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


def fetch_soup(session: requests.Session, url: str) -> Optional[BeautifulSoup]:
    try:
        resp = session.get(url, timeout=25)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:
        LOGGER.error("Error fetching %s: %s", url, exc)
        return None


def get_content_container(soup: BeautifulSoup) -> BeautifulSoup:
    """
    سعی می‌کند فقط ناحیه محتوای اصلی (نه منو و سایدبار) را بگیرد.
    اگر پیدا نشد، کل صفحه را برمی‌گرداند.
    """
    candidates = [
        {"id": "dle-content"},
        {"id": "content"},
        {"id": "main"},
        {"id": "page"},
        {"class_": "content"},
        {"class_": "main"},
    ]
    for kw in candidates:
        el = soup.find(**kw)
        if el:
            return el
    return soup


def looks_like_post_url(base_url: str, href: str) -> Optional[str]:
    """
    بررسی می‌کند که یک href، لینک واقعیِ یک پست (کتاب/مجله/موزیک/...) است یا نه.
    شرط‌ها:
      - روی همان دامنه باشد
      - پسوند .html داشته باشد
      - مسیرش با یکی از CONTENT_PREFIXES شروع شود
      - لینکِ ریشه‌ی دسته‌ها مثل /music یا /ebooks خودش حساب نشود
    """
    if not href or href.startswith("#"):
        return None

    full = urljoin(base_url, href)
    parsed = urlparse(full)
    base_parsed = urlparse(base_url)

    # روی همان دامنه
    if parsed.netloc and parsed.netloc != base_parsed.netloc:
        return None

    path = parsed.path or "/"
    path_lower = path.lower()

    if not path_lower.endswith(".html"):
        return None

    for prefix in CONTENT_PREFIXES:
        p = prefix.rstrip("/").lower()
        if path_lower.startswith(p + "/"):
            return full

    return None


def extract_posts_from_blog(
    soup: BeautifulSoup,
    blog: BlogConfig,
) -> List[PostInfo]:
    """
    فقط لینک‌های واقعی پست‌ها را از داخل ناحیه محتوای بلاگ استخراج می‌کند.
    """
    container = get_content_container(soup)
    posts: List[PostInfo] = []
    seen_urls: Set[str] = set()

    for a in container.find_all("a", href=True):
        full_url = looks_like_post_url(blog.url, a["href"])
        if not full_url:
            continue

        if full_url in seen_urls:
            continue

        title = a.get_text(strip=True)
        if not title:
            continue

        seen_urls.add(full_url)
        posts.append(PostInfo(blog=blog.name, title=title, details="", url=full_url))

    return posts


# -----------------------
# عنوان و جزئیات از صفحه پست
# -----------------------

def extract_title_from_post(soup: BeautifulSoup) -> str:
    """
    تلاش برای پیدا کردن عنوان واقعی از صفحه پست (h1، h2، title).
    """
    for sel in ["h1", "h2", "h3"]:
        tag = soup.find(sel)
        if tag:
            text = tag.get_text(strip=True)
            if text:
                return text

    if soup.title:
        t = soup.title.get_text(strip=True)
        # اگر توی title ساختار سایت هم بود، سعی می‌کنیم فقط بخش عنوان را برداریم
        for sep in [" » ", " | ", " - "]:
            if sep in t:
                t = t.split(sep, 1)[0].strip()
                break
        return t

    return ""


def extract_details_from_post(soup: BeautifulSoup) -> str:
    """
    از صفحه پست، یک خط حاوی اطلاعات فرمت/حجم/ISBN/... را پیدا می‌کند.
    مثل:
      English | 2026 | ISBN: 1350557420 | 249 pages | True PDF EPUB | 8.72 MB
    """

    # کلمات کلیدی‌ای که معمولا در خط اطلاعات کتاب/فایل دیده می‌شوند
    keywords = [
        "isbn",
        "pages",
        "true pdf",
        "pdf",
        "epub",
        "mobi",
        "azw",
        "djvu",
        "fb2",
        "mp3",
        "flac",
        "m4b",
        "mkv",
        "mp4",
        "avi",
        "mb",
        "gb",
        "kb",
        "مگابایت",
        "گیگابایت",
        "کیلوبایت",
    ]

    candidates: List[str] = []

    # اول دنبال خطوطی می‌گردیم که هم طول مناسب دارند، هم '|' دارند هم شامل یکی از keywordها هستند
    for text in soup.stripped_strings:
        t = " ".join(text.split())
        if len(t) < 20 or len(t) > 300:
            continue

        lower = t.lower()
        if "|" in t and any(k in lower for k in keywords):
            candidates.append(t)

    # اگر خطی که شامل ISBN است پیدا شد، همان را ترجیح می‌دهیم
    for c in candidates:
        if "isbn" in c.lower():
            return c

    if candidates:
        return candidates[0]

    # اگر هنوز چیزی نداریم، دوباره ولی بدون شرط '|'، فقط بر اساس کی‌ورد
    fallback: List[str] = []
    for text in soup.stripped_strings:
        t = " ".join(text.split())
        if len(t) < 20 or len(t) > 300:
            continue

        lower = t.lower()
        if any(k in lower for k in keywords):
            fallback.append(t)

    for c in fallback:
        if "isbn" in c.lower():
            return c

    if fallback:
        return fallback[0]

    return "—"


# -----------------------
# استخراج و دانلود کاور
# -----------------------

def extract_cover_image_url(soup: BeautifulSoup, base_url: str) -> Optional[str]:
    """
    از صفحه پست، سعی می‌کند URL کاور/پوستر را پیدا کند.
    اولویت:
      1) تصویری که از هاست‌های pixhost.* می‌آید (ترجیحاً /avaxhome/ و medium)
      2) سایر تصویرها، به شرطی که بنر/حمایت/لوگو نباشند
    """

    container = get_content_container(soup)

    # 1) <a href="https://pixhost.icu/..."><img src="..."></a>
    for a in container.find_all("a", href=True):
        href = a["href"]
        if "pixhost." in href:
            img = a.find("img", src=True)
            if not img:
                continue
            src = img.get("src") or img.get("data-src")
            if not src:
                continue
            if src.startswith("http://") or src.startswith("https://"):
                return src
            return urljoin(base_url, src)

    # 2) هر <img> که src شامل pixhost.* باشد
    pixhost_candidates: List[str] = []
    for img in container.find_all("img", src=True):
        src = img.get("src") or img.get("data-src")
        if not src:
            continue
        src_l = src.lower()
        if "pixhost." in src_l:
            pixhost_candidates.append(src)

    def pick_best(cands: List[str]) -> Optional[str]:
        if not cands:
            return None
        # ترجیح: شامل /avaxhome/
        for c in cands:
            if "/avaxhome/" in c.lower():
                return c
        # ترجیح بعدی: medium
        for c in cands:
            if "medium" in c.lower():
                return c
        # در نهایت: اولین
        return cands[0]

    best_pix = pick_best(pixhost_candidates)
    if best_pix:
        if best_pix.startswith("http://") or best_pix.startswith("https://"):
            return best_pix
        return urljoin(base_url, best_pix)

    # 3) fallback: سایر تصویرها، با فیلتر روی پسوند و حذف بنر/لوگو/حمایت
    good_candidates: List[str] = []
    for img in container.find_all("img", src=True):
        src = img.get("src") or img.get("data-src")
        if not src:
            continue
        src_l = src.lower()
        if not any(ext in src_l for ext in [".jpg", ".jpeg", ".png", ".webp", ".gif"]):
            continue
        if any(bad in src_l for bad in UNWANTED_IMAGE_SUBSTRINGS):
            continue
        good_candidates.append(src)

    if good_candidates:
        src = good_candidates[0]
        if src.startswith("http://") or src.startswith("https://"):
            return src
        return urljoin(base_url, src)

    return None


def build_slug_from_url(url: str) -> str:
    """
    از URL پست، یک نام فایل امن می‌سازد مثل:
    /ebooks/1350557420.html -> ebooks_1350557420
    """
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    if not path:
        return "post"

    parts = path.split("/")
    last = parts[-1]
    if "." in last:
        last = last.rsplit(".", 1)[0]
    parts[-1] = last
    slug = "_".join(parts)
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", slug)
    return slug or "post"


def download_cover_image(
    session: requests.Session, soup: BeautifulSoup, post_url: str
) -> Optional[str]:
    """
    تصویر کاور را دانلود کرده و داخل zavat_data/images ذخیره می‌کند.
    خروجی: مسیر نسبی برای استفاده داخل Markdown مثل ../images/ebooks_1350557420.jpg
    """
    img_url = extract_cover_image_url(soup, post_url)
    if not img_url:
        return None

    parsed = urlparse(img_url)
    ext = Path(parsed.path).suffix.lower()
    if ext not in [".jpg", ".jpeg", ".png", ".webp", ".gif"]:
        ext = ".jpg"

    slug = build_slug_from_url(post_url)
    filename = f"{slug}{ext}"
    dest = IMAGES_DIR / filename

    if not dest.exists():
        try:
            resp = session.get(img_url, timeout=25)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            LOGGER.info("Downloaded image for %s -> %s", post_url, dest)
        except Exception as exc:
            LOGGER.error("Error downloading image %s: %s", img_url, exc)
            return None
    else:
        LOGGER.info("Image already exists for %s -> %s", post_url, dest)

    # مسیر نسبی از دید پوشه md/ (یک سطح بالاتر + images)
    rel_path = Path("..") / "images" / filename
    return rel_path.as_posix()


# -----------------------
# نوشتن لاگ روزانه (متنی)
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
# نوشتن لاگ روزانه (Markdown با تصویر)
# -----------------------

def append_posts_to_daily_markdown(posts: List[PostInfo]) -> None:
    if not posts:
        return

    now = datetime.now(TZ)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")
    md_path = MD_DIR / f"{date_str}.md"

    is_new = not md_path.exists()

    with md_path.open("a", encoding="utf-8") as f:
        if is_new:
            f.write(f"# Zavat feed - {date_str}\n\n")

        for post in posts:
            f.write("---\n\n")
            f.write(f"**Time:** {time_str} ({TZ.key})  \n")
            f.write(f"**Blog:** {post.blog}  \n\n")
            f.write(f"**Title:** {post.title}  \n\n")
            f.write(f"**Details:** {post.details}  \n\n")
            f.write(f"**Link:** {post.url}  \n\n")
            if post.image_rel_path:
                f.write(f"![cover]({post.image_rel_path})\n\n")


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

        for post in posts:
            if post.url in seen_urls:
                continue

            LOGGER.info("New post detected: %s", post.url)
            post_soup = fetch_soup(session, post.url)
            if post_soup is not None:
                # عنوان را اگر بشود از خود صفحه پست بهتر دربیاوریم
                new_title = extract_title_from_post(post_soup)
                if new_title:
                    post.title = new_title

                post.details = extract_details_from_post(post_soup)

                # دانلود کاور و تنظیم مسیر نسبی برای Markdown
                post.image_rel_path = download_cover_image(session, post_soup, post.url)
            else:
                post.details = "—"
                post.image_rel_path = None

            new_posts.append(post)
            seen_urls.add(post.url)

    if not new_posts:
        LOGGER.info("No new posts found.")
    else:
        LOGGER.info("Found %d new posts.", len(new_posts))
        append_posts_to_daily_log(new_posts)
        append_posts_to_daily_markdown(new_posts)

    state["seen_urls"] = sorted(seen_urls)
    save_state(state)


if __name__ == "__main__":
    main()
