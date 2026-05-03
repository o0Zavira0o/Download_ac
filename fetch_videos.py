import os
import requests
from datetime import datetime, timedelta, timezone
import re

# ---------------- تنظیمات اصلی ----------------

API_KEY = os.environ["YOUTUBE_API_KEY"]

# این‌جا کوئری‌های سرچ را می‌گذاری (هر چی خواستی بعداً اضافه کن)
SEARCH_QUERIES = [
    "solidworks tutorial",
    "solidworks project",
    "Cad design",
    "Restoration",
]

# چند ساعت قبل را می‌خواهی؟ (برای 24 ساعت گذشته)
TIME_WINDOW_HOURS = 24

# حداقل مدت ویدیو (ثانیه) — 4 دقیقه = 240 ثانیه
MIN_DURATION_SECONDS = 4 * 60

# حداکثر چند صفحه نتیجه برای هر کوئری (هر صفحه حداکثر 50 ویدیو)
MAX_PAGES_PER_QUERY = 4  # یعنی حداکثر ~200 ویدیو در 24 ساعت برای هر کوئری

# ------------------------------------------------


def iso_24h_ago():
    """زمان 24 ساعت قبل به فرمت مورد نیاز YouTube API (RFC3339)"""
    dt = datetime.now(timezone.utc) - timedelta(hours=TIME_WINDOW_HOURS)
    # مثال خروجی: "2024-05-03T12:34:56Z"
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso_duration(duration_str):
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
    """تقسیم لیست به تکه‌های حداکثر n تایی (برای call به API)"""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def search_video_ids_for_query(query):
    """
    برای یک کوئری، ویدیوهای منتشرشده در 24 ساعت گذشته را
    (فقط IDها) می‌آورد، با مرتب‌سازی بر اساس تاریخ انتشار (جدیدترین اول).
    """
    url = "https://www.googleapis.com/youtube/v3/search"
    published_after = iso_24h_ago()

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


def fetch_videos_details(video_ids):
    """
    از روی لیست ID، اطلاعات کامل ویدیوها را (عنوان، مدت، تاریخ، thumbnail، ...)
    می‌آورد و ویدیوهایی که کمتر از MIN_DURATION_SECONDS هستند را حذف می‌کند.
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
            duration_str = content["duration"]
            duration_seconds = parse_iso_duration(duration_str)

            if duration_seconds < MIN_DURATION_SECONDS:
                continue

            video_id = item["id"]
            title = snippet["title"].replace("\n", " ")
            published_at = snippet["publishedAt"]  # "YYYY-MM-DDTHH:MM:SSZ"
            # تبدیل به فرمت خواناتر:
            published_human = (
                published_at.replace("T", " ")
                            .replace("Z", " (UTC)")
            )

            thumb = (
                snippet.get("thumbnails", {})
                       .get("high", {})
                       .get("url")
                or snippet.get("thumbnails", {})
                          .get("default", {})
                          .get("url", "")
            )

            videos.append({
                "id": video_id,
                "title": title,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "published_raw": published_at,
                "published": published_human,
                "duration_seconds": duration_seconds,
                "duration_minutes": round(duration_seconds / 60, 1),
                "thumbnail": thumb,
            })

    # مرتب‌سازی بر اساس زمان انتشار (جدیدترها اول)
    videos.sort(key=lambda v: v["published_raw"], reverse=True)
    return videos


def generate_markdown(results_by_query):
    """تولید متن Markdown از نتایج همه‌ی کوئری‌ها."""
    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    now_str = now_utc.isoformat().replace("+00:00", "Z")

    lines = []
    lines.append("# ویدیوهای جدید یوتیوب مربوط به سالیدورکس (۲۴ ساعت گذشته)\n")
    lines.append(f"_به‌روزرسانی: {now_str}_\n")

    for query, items in results_by_query.items():
        lines.append(f"\n## جستجو: `{query}`\n")
        if not items:
            lines.append("> هیچ ویدیویی برای این جستجو در ۲۴ ساعت گذشته پیدا نشد.\n")
            continue

        for v in items:
            lines.append(
                f"- **تاریخ انتشار:** {v['published']}  \n"
                f"  **مدت:** حدود {v['duration_minutes']} دقیقه  \n"
                f"  **عنوان:** [{v['title']}]({v['url']})  \n"
                f"  ![]({v['thumbnail']})\n"
            )

    return "\n".join(lines)


def main():
    results_by_query = {}

    for q in SEARCH_QUERIES:
        print(f"جستجو برای: {q}")
        ids = search_video_ids_for_query(q)
        videos = fetch_videos_details(ids)
        results_by_query[q] = videos
        print(f"تعداد ویدیوهای قبول‌شده (بیش از ۴ دقیقه): {len(videos)}")

    markdown = generate_markdown(results_by_query)

    output_path = "videos.md"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(markdown)

    print(f"نتایج در فایل {output_path} ذخیره شد.")


if __name__ == "__main__":
    main()
