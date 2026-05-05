import os
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# ===== تنظیمات کلی =====
API_KEY = os.environ["YOUTUBE_API_KEY"]

# بازه‌ی زمانی جستجو (ساعت گذشته)
TIME_WINDOW_HOURS = 24

# حداقل طول ویدیوهای "اصلی"
MIN_LONG_SECONDS = 4 * 60  # ۴ دقیقه

# حداکثر طول برای این‌که یک ویدیو را "Short" در نظر بگیریم
# توجه: API فلگ رسمی برای Shorts ندارد، این‌جا فرض کرده‌ایم ویدیوهای <= ۶۰ ثانیه Shorts هستند.
MAX_SHORT_SECONDS = 60

# حداکثر تعداد صفحات نتایج سرچ برای هر کوئری/کانال (هر صفحه حداکثر ۵۰ ویدیو)
MAX_PAGES_PER_QUERY = 4

# مسیر پوشه‌ی خروجی (تمام گزارش‌ها این‌جا می‌آیند)
OUTPUT_ROOT = Path("reports")

# فایل ثبت ویدیوهایی که قبلاً گزارش شده‌اند (برای جلوگیری از تکرار)
SEEN_FILE = OUTPUT_ROOT / "seen_videos.json"

# موضوعات (تاپیک‌ها) و کلیدواژه‌ها/کانال‌ها
TOPICS = {
    "solidworks": {
        "title": "آموزش‌ها و پروژه‌های SolidWorks",
        "queries": [
            "solidworks",
            "cad design",
        ],
        "channels": [
            # مثال: اگر یک کانال سالیدورکس مورد علاقه داری، این‌طور اضافه کن:
            # {
            #     "channel_id": "UCxxxxxxxxxxxx",  # آیدی کانال
            #     "name": "Awesome SolidWorks Channel",
            # },
        ],
    },
    "asmr": {
        "title": "ویدیوهای ASMR",
        "queries": [
            "ASMR",
            "no talking",
        ],
        "channels": [
            # این‌جا هم اگر کانال ASMR خاصی را خواستی مانیتور کنی اضافه کن
        ],
    },
    "CAD": {
        "title": "ویدیوهای CAD Design",
        "queries": [
            "Construction planning",
        ],
        "channels": [
            # این‌جا هم اگر کانال ASMR خاصی را خواستی مانیتور کنی اضافه کن
        ],
    },
}
# ===== پایان تنظیمات =====


def iso_24h_ago(now_utc: datetime) -> str:
    """زمان ۲۴ ساعت قبل به فرمت RFC3339 (برای پارامتر publishedAfter)."""
    dt = now_utc - timedelta(hours=TIME_WINDOW_HOURS)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso_duration(duration_str: str) -> int:
    """
    تبدیل مدت زمان ISO8601 یوتیوب (مثلاً PT5M10S یا PT1H2M3S)
    به تعداد ثانیه.
    """
    pattern = r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?"
    m = re.match(pattern, duration_str)
    if not m:
        return 0
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    seconds = int(m.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def chunked(lst, n):
    """تقسیم لیست به تکه‌های حداکثر n‌تایی (برای درخواست‌های API)."""
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def load_seen_ids() -> set:
    """خواندن لیست ویدیوهایی که قبلاً در گزارش‌ها آمده‌اند."""
    if SEEN_FILE.exists():
        with SEEN_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("seen_ids", []))
    return set()


def save_seen_ids(seen_ids: set):
    """ذخیره‌ی لیست به‌روزشده‌ی ویدیوهای دیده‌شده."""
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    with SEEN_FILE.open("w", encoding="utf-8") as f:
        json.dump({"seen_ids": sorted(list(seen_ids))}, f, ensure_ascii=False, indent=2)


def get_today_dir(now_utc: datetime) -> Path:
    """پوشه‌ی مخصوص امروز را (مثلاً reports/2026-05-03/) می‌سازد/برمی‌گرداند."""
    date_str = now_utc.date().isoformat()
    dir_path = OUTPUT_ROOT / date_str
    dir_path.mkdir(parents=True, exist_ok=True)
    return dir_path


def search_video_ids_for_query(query: str, published_after: str) -> list:
    """برگرداندن لیست ID ویدیوها برای یک کوئری متنی در بازه‌ی ۲۴ ساعت گذشته."""
    url = "https://www.googleapis.com/youtube/v3/search"
    all_ids = []
    next_page_token = None
    pages = 0

    while True:
        params = {
            "key": API_KEY,
            "part": "snippet",
            "type": "video",
            "order": "date",
            "q": query,
            "publishedAfter": published_after,
            "maxResults": 50,
        }
        if next_page_token:
            params["pageToken"] = next_page_token

        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        for item in data.get("items", []):
            vid = item["id"]["videoId"]
            all_ids.append(vid)

        next_page_token = data.get("nextPageToken")
        pages += 1
        if not next_page_token or pages >= MAX_PAGES_PER_QUERY:
            break

    return all_ids


def search_video_ids_for_channel(channel_id: str, published_after: str) -> list:
    """برگرداندن لیست ID ویدیوهای یک کانال در ۲۴ ساعت گذشته."""
    url = "https://www.googleapis.com/youtube/v3/search"
    all_ids = []
    next_page_token = None
    pages = 0

    while True:
        params = {
            "key": API_KEY,
            "part": "snippet",
            "type": "video",
            "order": "date",
            "channelId": channel_id,
            "publishedAfter": published_after,
            "maxResults": 50,
        }
        if next_page_token:
            params["pageToken"] = next_page_token

        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        for item in data.get("items", []):
            vid = item["id"]["videoId"]
            all_ids.append(vid)

        next_page_token = data.get("nextPageToken")
        pages += 1
        if not next_page_token or pages >= MAX_PAGES_PER_QUERY:
            break

    return all_ids


def fetch_videos_details(video_ids: list) -> list:
    """
    گرفتن جزئیات کامل ویدیوها (عنوان، کانال، تاریخ، thumbnail، مدت).
    در این تابع هنوز فیلتر طول اعمال نمی‌شود؛ فقط اطلاعات خام آماده می‌شود.
    """
    if not video_ids:
        return []

    url = "https://www.googleapis.com/youtube/v3/videos"
    videos = []

    for chunk in chunked(video_ids, 50):
        params = {
            "key": API_KEY,
            "part": "snippet,contentDetails",
            "id": ",".join(chunk),
            "maxResults": 50,
        }
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        for item in data.get("items", []):
            snippet = item["snippet"]
            content = item["contentDetails"]

            duration_str = content.get("duration", "PT0S")
            duration_seconds = parse_iso_duration(duration_str)
            duration_minutes = round(duration_seconds / 60.0, 1)

            video_id = item["id"]
            title = snippet["title"].replace("\n", " ")
            published_at = snippet["publishedAt"]  # "YYYY-MM-DDTHH:MM:SSZ"
            published_human = published_at.replace("T", " ").replace("Z", " (UTC)")

            channel_title = snippet.get("channelTitle", "Unknown Channel")
            channel_id = snippet.get("channelId", "")
            channel_url = f"https://www.youtube.com/channel/{channel_id}" if channel_id else ""

            thumb = (
                snippet.get("thumbnails", {})
                .get("high", {})
                .get("url")
                or snippet.get("thumbnails", {})
                .get("default", {})
                .get("url", "")
            )

            videos.append(
                {
                    "id": video_id,
                    "title": title,
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "published_raw": published_at,
                    "published": published_human,
                    "duration_seconds": duration_seconds,
                    "duration_minutes": duration_minutes,
                    "thumbnail": thumb,
                    "channel_title": channel_title,
                    "channel_url": channel_url,
                }
            )

    # مرتب‌سازی بر اساس زمان انتشار (جدیدترها اول)
    videos.sort(key=lambda v: v["published_raw"], reverse=True)
    return videos


def generate_markdown(topic_title: str, kind_label: str, videos: list, now_iso: str) -> str:
    """
    ساخت متن Markdown برای یک موضوع و یک نوع (ویدیوهای معمولی / Shorts).
    """
    lines = []
    lines.append(f"# {topic_title} — {kind_label}\n")
    lines.append(f"_بازه‌ی جستجو: ۲۴ ساعت گذشته تا {now_iso}_\n")

    if not videos:
        lines.append("\n> در این بازه ویدیوی مناسبی پیدا نشد.\n")
        return "\n".join(lines)

    for v in videos:
        lines.append(
            f"- **تاریخ انتشار:** {v['published']} — "
            f"**کانال:** [{v['channel_title']}]({v['channel_url']})  \n"
            f"  **مدت:** حدود {v['duration_minutes']} دقیقه  \n"
            f"  **عنوان:** [{v['title']}]({v['url']})  \n"
            f"  ![]({v['thumbnail']})\n"
        )

    return "\n".join(lines)


def main():
    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    now_iso = now_utc.isoformat().replace("+00:00", "Z")
    published_after = iso_24h_ago(now_utc)

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    today_dir = get_today_dir(now_utc)

    seen_ids = load_seen_ids()
    new_ids = set()

    print(f"شروع جستجو. اکنون (UTC): {now_iso}")
    print(f"جستجو برای ویدیوهای بعد از: {published_after}")

    for topic_key, cfg in TOPICS.items():
        topic_title = cfg.get("title", topic_key)
        print(f"\n=== موضوع: {topic_key} — {topic_title} ===")

        candidate_ids = set()

        # 1) جستجوی متنی
        for q in cfg.get("queries", []):
            print(f"  جستجو با کوئری: {q}")
            ids = search_video_ids_for_query(q, published_after)
            print(f"    تعداد ویدیوهای یافت‌شده: {len(ids)}")
            candidate_ids.update(ids)

        # 2) بررسی کانال‌ها
        for ch in cfg.get("channels", []):
            ch_id = ch["channel_id"]
            ch_name = ch.get("name", ch_id)
            print(f"  بررسی کانال: {ch_name} ({ch_id})")
            ids = search_video_ids_for_channel(ch_id, published_after)
            print(f"    تعداد ویدیوهای یافت‌شده: {len(ids)}")
            candidate_ids.update(ids)

        candidate_ids = list(candidate_ids)
        print(f"  مجموع ویدیوهای یکتا (قبل از گرفتن جزئیات): {len(candidate_ids)}")

        # اگر چیزی پیدا نشد، باز هم فایل‌های خالی (با پیام) بسازیم
        if not candidate_ids:
            long_md = generate_markdown(
                topic_title,
                "ویدیوهای معمولی (بیش از ۴ دقیقه)",
                [],
                now_iso,
            )
            shorts_md = generate_markdown(
                topic_title,
                "ویدیوهای کوتاه (Shorts، کمتر از ۶۰ ثانیه)",
                [],
                now_iso,
            )
            (today_dir / f"{topic_key}.md").write_text(long_md, encoding="utf-8")
            (today_dir / f"{topic_key}_shorts.md").write_text(shorts_md, encoding="utf-8")
            continue

        # گرفتن جزئیات همه‌ی این ویدیوها
        videos_all = fetch_videos_details(candidate_ids)
        print(f"  تعداد ویدیوها پس از گرفتن جزئیات: {len(videos_all)}")

        # جدا کردن ویدیوهای طولانی و Shorts بر اساس مدت
        videos_long = [
            v for v in videos_all if v["duration_seconds"] >= MIN_LONG_SECONDS
        ]
        videos_shorts = [
            v for v in videos_all if v["duration_seconds"] <= MAX_SHORT_SECONDS
        ]

        print(f"  ویدیوهای بالای ۴ دقیقه (قبل از حذف تکراری‌ها): {len(videos_long)}")
        print(
            f"  ویدیوهای کوتاه <= {MAX_SHORT_SECONDS} ثانیه "
            f"(قبل از حذف تکراری‌ها): {len(videos_shorts)}"
        )

        # حذف ویدیوهایی که قبلاً گزارش شده‌اند (برای جلوگیری از تکرار در کل پروژه)
        videos_long = [v for v in videos_long if v["id"] not in seen_ids]
        videos_shorts = [v for v in videos_shorts if v["id"] not in seen_ids]

        print(f"  ویدیوهای بالای ۴ دقیقه (بعد از حذف تکراری‌ها): {len(videos_long)}")
        print(f"  ویدیوهای کوتاه (بعد از حذف تکراری‌ها): {len(videos_shorts)}")

        # افزودن ID ویدیوهای جدید به مجموعه‌ی new_ids
        for v in videos_long + videos_shorts:
            new_ids.add(v["id"])

        # تولید فایل‌های Markdown
        long_md = generate_markdown(
            topic_title,
            "ویدیوهای معمولی (بیش از ۴ دقیقه)",
            videos_long,
            now_iso,
        )
        shorts_md = generate_markdown(
            topic_title,
            f"ویدیوهای کوتاه (Shorts، کمتر از {MAX_SHORT_SECONDS} ثانیه)",
            videos_shorts,
            now_iso,
        )

        long_path = today_dir / f"{topic_key}.md"
        shorts_path = today_dir / f"{topic_key}_shorts.md"

        long_path.write_text(long_md, encoding="utf-8")
        shorts_path.write_text(shorts_md, encoding="utf-8")

        print(f"  فایل خروجی (ویدیوهای معمولی): {long_path}")
        print(f"  فایل خروجی (Shorts): {shorts_path}")

    # به‌روزرسانی فایل seen_videos
    if new_ids:
        seen_ids.update(new_ids)
        save_seen_ids(seen_ids)
        print(f"\nتعداد ویدیوهای جدید اضافه‌شده به فهرست تماشا‌شده‌ها: {len(new_ids)}")
    else:
        print("\nهیچ ویدیوی جدیدی نسبت به گزارش‌های قبلی پیدا نشد.")

    print("\nپایان کار.")


if __name__ == "__main__":
    main()
