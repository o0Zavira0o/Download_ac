import os
import re
import time
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

# ========== تنظیمات کلی ==========

API_KEY = os.environ["YOUTUBE_API_KEY"]

# حداکثر تعداد ویدیو برای هر کانال
MAX_VIDEOS_PER_CHANNEL = 150

# پوشه‌ای که فایل‌های کانال‌ها در آن ذخیره می‌شود
OUTPUT_DIR = Path("channels")

# لیست کانال‌ها
# می‌توانی فقط url را بنویسی؛ اسکریپت خودش channel_id را از آن درمی‌آورد
CHANNELS = [
    {
        "url": "https://www.youtube.com/channel/UCDS54PfYTOOgOSexAYc9qbw",
        # "label": "هر توضیحی که خودت دوست داری (اختیاری)"
    },
    {
        "url": "https://www.youtube.com/channel/UCFnsKhMbsX6IV_KPsMaA5PQ",
    },
    {
        "url": "https://www.youtube.com/channel/UC7biUF9zCSHU_yXWbpCTE6Q",
    },
    {
        "url": "https://www.youtube.com/channel/UCjd_zIvYtQymk0dPx3vTJcA",
    },
    {
        "url": "https://www.youtube.com/channel/UCMOpS6plb9Utzie8RdzcZvA",
    },
    {
        "url": "https://www.youtube.com/channel/UCi_pluZoV81wOpEHXpfJelw",
    },    
    {
        "url": "https://www.youtube.com/channel/UCPnGEuRnpS1evWl39UKjXFQ",
    },
    {
        "url": "https://www.youtube.com/channel/UCH2-aT4yIrfuuXkcgsuisYg",
    },
    {
        "url": "https://www.youtube.com/channel/UCRmLnVaHsSAH0HkXfeoxG6w",
    },
    {
        "url": "https://www.youtube.com/channel/UCDxi4oWOK_VUFmHBLnHPnXA",
    },
    {
        "url": "https://www.youtube.com/channel/UCpon2fZnhSm-i39pfel-0tw",
    },
    {
        "url": "https://www.youtube.com/channel/UCjQA-Opz3h7FECZbZjICypQ",
    },
    {
        "url": "https://www.youtube.com/channel/UCshX5HmfqJLs_8KSI-K0HXQ",
    },
    {
        "url": "https://www.youtube.com/channel/UCDmvwsI7-VjhJq3nlgXvByg",
    },
    {
        "url": "https://www.youtube.com/watch?v=ATjofahxGQs",
        # Pei Planet
    },
    {
        "url": "https://www.youtube.com/channel/UCYLbku4yKjzazuwF89NYC0A",
        # Origami☆Man
    },
    
    # https://www.youtube.com/channel/UCVc_jmqkqUNgEqBZ2pxOyyA


]

# ========== توابع کمکی ==========


def youtube_get(url, params, what="request", max_retries=3):
    """
    فراخوانی امن YouTube API:
    - لاگ‌کردن خطاها
    - چند بار تلاش مجدد برای خطاهای 5xx
    - برگرداندن None در صورت خطای جدی (مثل 403 quotaExceeded)
    """
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=30)
        except requests.RequestException as e:
            print(
                f"[ERROR] Network error while calling YouTube ({what}), "
                f"attempt {attempt}/{max_retries}: {e}"
            )
            if attempt == max_retries:
                return None
            time.sleep(2 * attempt)
            continue

        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError:
                print(f"[ERROR] Invalid JSON from YouTube ({what}) with status 200.")
                return None

        print(f"[ERROR] YouTube API ({what}) returned status {resp.status_code}")
        try:
            err_json = resp.json()
            print(
                "  Response body:",
                json.dumps(err_json, ensure_ascii=False, indent=2),
            )
        except Exception:
            print("  Raw response text:", resp.text)

        # برای خطاهای 5xx دوباره تلاش می‌کنیم
        if 500 <= resp.status_code < 600 and attempt < max_retries:
            time.sleep(2 * attempt)
            continue

        # برای 4xx و سایر حالت‌ها: قطع
        return None


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


def slugify(name: str) -> str:
    """
    تبدیل نام کانال به یک slug مناسب برای نام فایل.
    فاصله‌ها را به _ تبدیل می‌کند و کاراکترهای عجیب را حذف می‌کند.
    """
    name = (name or "").strip()
    if not name:
        return "channel"
    name = name.replace(" ", "_")
    name = re.sub(r"[^\w\-]+", "", name)
    return name[:50]


def extract_channel_id(cfg: dict) -> str:
    """
    از روی dict کانال، channel_id را پیدا می‌کند.
    یا مستقیماً از کلید channel_id یا از داخل url.
    """
    if "channel_id" in cfg and cfg["channel_id"]:
        return cfg["channel_id"]

    url = cfg.get("url", "")
    m = re.search(r"/channel/([A-Za-z0-9_\-]+)", url)
    if m:
        return m.group(1)

    raise ValueError(
        f"نمی‌توانم channel_id را از این کانال استخراج کنم: {cfg!r}\n"
        "لطفاً یا 'channel_id' را مستقیماً بده، یا url از نوع /channel/UC... باشد."
    )


# ========== گرفتن اطلاعات کانال و ویدیوها ==========


def get_channel_info(channel_id: str) -> dict | None:
    """
    گرفتن اطلاعات کانال و آیدی playlist آپلودهای آن.
    """
    url = "https://www.googleapis.com/youtube/v3/channels"
    params = {
        "key": API_KEY,
        "part": "snippet,contentDetails",
        "id": channel_id,
        "maxResults": 1,
    }
    data = youtube_get(url, params, what=f"channels.list (id={channel_id})")
    if data is None:
        return None

    items = data.get("items", [])
    if not items:
        print(f"[WARN] کانالی با id={channel_id} پیدا نشد.")
        return None

    item = items[0]
    snippet = item.get("snippet", {})
    cd = item.get("contentDetails", {})
    playlists = cd.get("relatedPlaylists", {})
    uploads_pl = playlists.get("uploads")

    if not uploads_pl:
        print(f"[WARN] برای کانال id={channel_id} playlist آپلودها پیدا نشد.")
        return None

    channel_title = snippet.get("title", channel_id)
    channel_url = f"https://www.youtube.com/channel/{channel_id}"

    return {
        "id": channel_id,
        "title": channel_title,
        "uploads_playlist_id": uploads_pl,
        "channel_url": channel_url,
    }


def fetch_channel_videos(channel_info: dict, limit: int) -> list:
    """
    گرفتن حداکثر 'limit' ویدیوی آخر کانال از playlist آپلودها
    + افزودن مدت زمان هر ویدیو.
    """
    playlist_id = channel_info["uploads_playlist_id"]
    url = "https://www.googleapis.com/youtube/v3/playlistItems"

    videos = []
    next_page_token = None

    while len(videos) < limit:
        params = {
            "key": API_KEY,
            "part": "snippet,contentDetails",
            "playlistId": playlist_id,
            "maxResults": 50,
        }
        if next_page_token:
            params["pageToken"] = next_page_token

        data = youtube_get(
            url,
            params,
            what=f"playlistItems.list (playlistId={playlist_id})",
        )
        if data is None:
            break

        for item in data.get("items", []):
            vid = item["contentDetails"]["videoId"]
            snippet = item.get("snippet", {})
            title = snippet.get("title", "").replace("\n", " ")
            published_at = snippet.get("publishedAt", "")
            published_human = (
                published_at.replace("T", " ").replace("Z", " (UTC)")
                if published_at
                else ""
            )
            thumbs = snippet.get("thumbnails", {}) or {}
            thumb = (
                thumbs.get("high", {}).get("url")
                or thumbs.get("medium", {}).get("url")
                or thumbs.get("default", {}).get("url", "")
            )

            videos.append(
                {
                    "id": vid,
                    "title": title,
                    "published_raw": published_at,
                    "published": published_human,
                    "thumbnail": thumb,
                }
            )

            if len(videos) >= limit:
                break

        next_page_token = data.get("nextPageToken")
        if not next_page_token or len(videos) >= limit:
            break

    # گرفتن مدت زمان ویدیوها با videos.list
    add_durations_to_videos(videos)

    # مرتب‌سازی بر اساس تاریخ انتشار (جدیدترین اول)
    videos.sort(key=lambda v: v["published_raw"] or "", reverse=True)
    return videos


def add_durations_to_videos(videos: list):
    """
    برای لیست ویدیوها، با یک یا چند بار فراخوانی videos.list
    مدت هر ویدیو را (ثانیه و دقیقه) اضافه می‌کند.
    """
    if not videos:
        return

    ids = [v["id"] for v in videos]
    url = "https://www.googleapis.com/youtube/v3/videos"
    durations = {}

    for chunk in chunked(ids, 50):
        params = {
            "key": API_KEY,
            "part": "contentDetails",
            "id": ",".join(chunk),
            "maxResults": 50,
        }
        data = youtube_get(url, params, what="videos.list")
        if data is None:
            continue

        for item in data.get("items", []):
            vid = item["id"]
            cd = item.get("contentDetails", {})
            dur_str = cd.get("duration", "PT0S")
            seconds = parse_iso_duration(dur_str)
            durations[vid] = seconds

    for v in videos:
        sec = durations.get(v["id"], 0)
        v["duration_seconds"] = sec
        v["duration_minutes"] = round(sec / 60.0, 1) if sec else 0.0


# ========== ساخت Markdown ==========


def generate_markdown_for_channel(
    channel_info: dict, videos: list, now_iso: str
) -> str:
    title = channel_info["title"]
    url = channel_info["channel_url"]

    lines = []
    lines.append(f"# آرشیو ویدیوهای کانال {title}\n")
    lines.append(f"_آخرین به‌روزرسانی: {now_iso}_\n")
    lines.append(f"\nلینک کانال: [{title}]({url})\n")

    if not videos:
        lines.append("\n> برای این کانال هنوز ویدیویی پیدا نشد.\n")
        return "\n".join(lines)

    for v in videos:
        lines.append(
            f"\n- **تاریخ انتشار:** {v['published']}  \n"
            f"  **مدت:** حدود {v['duration_minutes']} دقیقه  \n"
            f"  **عنوان:** [{v['title']}]("
            f"https://www.youtube.com/watch?v={v['id']}"
            f")  \n"
            f"  ![]({v['thumbnail']})\n"
        )

    return "\n".join(lines)


# ========== main ==========


def main():
    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    now_iso = now_utc.isoformat().replace("+00:00", "Z")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"شروع به‌روزرسانی آرشیو کانال‌ها. اکنون (UTC): {now_iso}\n")

    for cfg in CHANNELS:
        try:
            channel_id = extract_channel_id(cfg)
        except ValueError as e:
            print(f"[ERROR] پرش از روی یکی از کانال‌ها: {e}")
            continue

        info = get_channel_info(channel_id)
        if info is None:
            print(f"[WARN] کانال با id={channel_id} رد شد.\n")
            continue

        print(f"=== کانال: {info['title']} ({channel_id}) ===")
        videos = fetch_channel_videos(info, MAX_VIDEOS_PER_CHANNEL)
        print(f"  تعداد ویدیوهای یافت‌شده: {len(videos)}")

        md_text = generate_markdown_for_channel(info, videos, now_iso)

        slug = slugify(info["title"])
        file_path = OUTPUT_DIR / f"{slug}__{channel_id}.md"
        file_path.write_text(md_text, encoding="utf-8")
        print(f"  فایل خروجی: {file_path}\n")

    print("پایان کار.")


if __name__ == "__main__":
    main()
