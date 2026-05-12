import os
import re
import time
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

# ========== تنظیمات کلی ==========

API_KEY = os.environ["YOUTUBE_API_KEY"]

# مقدار پیش‌فرض: حداکثر تعداد ویدیو برای هر کانال
MAX_VIDEOS_PER_CHANNEL = 150

# پوشه‌ای که فایل‌های کانال‌ها در آن ذخیره می‌شود
OUTPUT_DIR = Path("channels")

# لیست کانال‌ها
# بات الان این نوع URL ها را می‌فهمد:
#  - https://www.youtube.com/channel/UCxxxx
#  - https://www.youtube.com/watch?v=VIDEO_ID
#  - https://youtu.be/VIDEO_ID
#  - https://www.youtube.com/c/CustomName
#  - https://www.youtube.com/user/Username
#  - https://www.youtube.com/@handle
#
# اگر برای یک کانال خاص تعداد ویدیوهای بیشتری می‌خواهی، از کلید "num" یا "max_videos" استفاده کن.
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
        "url": "https://www.youtube.com/channel/UCjd_zIvYtQymk0dPx3vTJcA", "num": 1500,
        # cad cam tutorial by mahtabalam

    },
    {
        "url": "https://www.youtube.com/channel/UCMOpS6plb9Utzie8RdzcZvA",
        # Engineering Design

    },
    {
        "url": "https://www.youtube.com/channel/UCi_pluZoV81wOpEHXpfJelw",
    },    
    {
        "url": "https://www.youtube.com/channel/UCPnGEuRnpS1evWl39UKjXFQ",
        # nanoCADcom
    },
    {
        "url": "https://www.youtube.com/channel/UCH2-aT4yIrfuuXkcgsuisYg",
    },
    {
        "url": "https://www.youtube.com/channel/UCRmLnVaHsSAH0HkXfeoxG6w",
        # SolidWorks With Aryan Fallahi
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
    {
        "url": "https://www.youtube.com/c/ProfessorTedDiehl", "num": 1400,
    },

    {
        "url": "https://www.youtube.com/@hwaufranc", "num": 900,
    },
    {
        "url": "https://www.youtube.com/channel/UCbxb2fqe9oNgglAoYqsYOtQ", "num": 1600,
    },
    {
        "url": "https://www.youtube.com/channel/UC8cP79GPyTLi3mrHjGd-a3w", "num": 300,
        # 225uthenthawai.91
    },
      
    
    
    
    
    # https://www.youtube.com/channel/UCVc_jmqkqUNgEqBZ2pxOyyA
]

# ========== توابع کمکی عمومی ==========


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
            print("  Response body:", json.dumps(err_json, ensure_ascii=False, indent=2))
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


# ========== استخراج channel_id از انواع URL ==========


def extract_video_id_from_url(url: str) -> str | None:
    """اگر URL یک ویدیو باشد، video_id را برمی‌گرداند."""
    # شکل معمول watch?v=...
    m = re.search(r"[?&]v=([^&]+)", url)
    if m:
        return m.group(1)

    # شکل youtu.be/VIDEO_ID
    m = re.search(r"youtu\.be/([^?&]+)", url)
    if m:
        return m.group(1)

    return None


def get_channel_id_from_video(video_id: str) -> str | None:
    """گرفتن channelId از روی videoId."""
    url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        "key": API_KEY,
        "part": "snippet",
        "id": video_id,
        "maxResults": 1,
    }
    data = youtube_get(url, params, what=f"videos.list (videoId={video_id})")
    if data is None:
        return None

    items = data.get("items", [])
    if not items:
        print(f"[WARN] ویدیویی با id={video_id} پیدا نشد.")
        return None

    snippet = items[0].get("snippet", {})
    ch_id = snippet.get("channelId")
    if not ch_id:
        print(f"[WARN] برای ویدیو {video_id} channelId پیدا نشد.")
    return ch_id


def search_channel_id_by_query(q: str) -> str | None:
    """
    جستجوی یک کانال با کوئری (برای /c/Name، /@handle، /user/Name و ...).
    ساده‌ترین راه: search.list با type=channel و q=...
    """
    url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "key": API_KEY,
        "part": "snippet",
        "type": "channel",
        "q": q,
        "maxResults": 1,
    }
    data = youtube_get(url, params, what=f"search (channel query='{q}')")
    if data is None:
        return None

    items = data.get("items", [])
    if not items:
        print(f"[WARN] با جستجوی '{q}' هیچ کانالی پیدا نشد.")
        return None

    ch_id = items[0]["id"]["channelId"]
    return ch_id


def extract_channel_id(cfg: dict) -> str:
    """
    از روی dict کانال، channel_id را پیدا می‌کند.
    ورودی می‌تواند:
      - channel_id مستقیم
      - url با:
        * /channel/UC...
        * /watch?v=...
        * youtu.be/...
        * /@handle
        * /c/Name
        * /user/Name
    """
    if "channel_id" in cfg and cfg["channel_id"]:
        return cfg["channel_id"]

    url = cfg.get("url", "")
    if not url:
        raise ValueError(f"برای این کانال هیچ url یا channel_id داده نشده: {cfg!r}")

    # 1) مستقیم /channel/UC...
    m = re.search(r"/channel/([A-Za-z0-9_\-]+)", url)
    if m:
        return m.group(1)

    # 2) اگر URL ویدیو بود
    vid = extract_video_id_from_url(url)
    if vid:
        print(f"  URL به‌عنوان ویدیو شناخته شد، videoId = {vid} — در حال گرفتن channelId ...")
        ch_id = get_channel_id_from_video(vid)
        if ch_id:
            print(f"  برای ویدیو {vid}، channelId = {ch_id}")
            return ch_id
        else:
            raise ValueError(f"نتوانستم channelId را از روی ویدیو {vid} به‌دست بیاورم.")

    # 3) URL با @handle
    m = re.search(r"/@([^/?]+)", url)
    if m:
        handle = m.group(1)
        print(f"  URL شامل handle است: @{handle} — در حال جستجوی کانال ...")
        ch_id = search_channel_id_by_query(handle)
        if ch_id:
            print(f"  handle @{handle} به channelId = {ch_id} نگاشت شد.")
            return ch_id
        else:
            raise ValueError(f"با handle @{handle} نتوانستم کانالی پیدا کنم.")

    # 4) /c/Name
    m = re.search(r"/c/([^/?]+)", url)
    if m:
        name = m.group(1)
        print(f"  URL شامل /c/{name} است — در حال جستجوی کانال ...")
        ch_id = search_channel_id_by_query(name)
        if ch_id:
            print(f"  /c/{name} به channelId = {ch_id} نگاشت شد.")
            return ch_id
        else:
            raise ValueError(f"با /c/{name} نتوانستم کانالی پیدا کنم.")

    # 5) /user/Name
    m = re.search(r"/user/([^/?]+)", url)
    if m:
        name = m.group(1)
        print(f"  URL شامل /user/{name} است — در حال جستجوی کانال ...")
        ch_id = search_channel_id_by_query(name)
        if ch_id:
            print(f"  /user/{name} به channelId = {ch_id} نگاشت شد.")
            return ch_id
        else:
            raise ValueError(f"با /user/{name} نتوانستم کانالی پیدا کنم.")

    # اگر هیچ الگوی شناخته‌شده‌ای نبود، یک تلاش کلی برای جستجو
    print(f"  URL ناشناخته؛ تلاش برای جستجوی کانال با خود URL به‌عنوان کوئری ...")
    ch_id = search_channel_id_by_query(url)
    if ch_id:
        print(f"  URL {url!r} به channelId = {ch_id} نگاشت شد.")
        return ch_id

    raise ValueError(
        f"نمی‌توانم channel_id را از این ورودی استخراج کنم: {cfg!r}\n"
        "لطفاً یا 'channel_id' را مستقیماً بده، یا یکی از URLهای استاندارد یوتیوب را."
    )


def get_channel_limit(cfg: dict) -> int:
    """
    برگرداندن تعداد ویدیوهایی که باید برای این کانال گرفته شود:
    - اگر 'num' یا 'max_videos' داده شده باشد، همان
    - وگرنه از MAX_VIDEOS_PER_CHANNEL
    """
    raw = cfg.get("num", cfg.get("max_videos", MAX_VIDEOS_PER_CHANNEL))
    try:
        n = int(raw)
        if n <= 0:
            raise ValueError
        return n
    except Exception:
        print(
            f"[WARN] مقدار num/max_videos برای کانال {cfg.get('url')} نامعتبر بود "
            f"({raw!r})؛ از مقدار پیش‌فرض {MAX_VIDEOS_PER_CHANNEL} استفاده می‌شود."
        )
        return MAX_VIDEOS_PER_CHANNEL


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
    اگر کانال کمتر از این تعداد ویدیو داشته باشد، همان موجودی را برمی‌گرداند.
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
            snippet = item.get("snippet", {}) or {}
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
        # اگر دیگر صفحه‌ای نبود، می‌فهمیم که کانال این‌قدر ویدیو بیشتر ندارد
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
    channel_info: dict, videos: list, now_iso: str, source_url: str | None = None
) -> str:
    title = channel_info["title"]
    url = channel_info["channel_url"]

    lines = []
    lines.append(f"# آرشیو ویدیوهای کانال {title}\n")
    lines.append(f"_آخرین به‌روزرسانی: {now_iso}_\n")

    lines.append(f"\n**لینک استاندارد کانال:** [{title}]({url})  \n")
    if source_url and source_url != url:
        lines.append(f"**آدرسی که به بات دادی:** {source_url}  \n")

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
        src_url = cfg.get("url", "")
        limit = get_channel_limit(cfg)

        try:
            channel_id = extract_channel_id(cfg)
        except ValueError as e:
            print(f"[ERROR] پرش از روی یکی از کانال‌ها: {e}")
            continue

        info = get_channel_info(channel_id)
        if info is None:
            print(f"[WARN] کانال با id={channel_id} رد شد.\n")
            continue

        print(
            f"=== کانال: {info['title']} ({channel_id}) — "
            f"درخواست حداکثر {limit} ویدیو ==="
        )
        videos = fetch_channel_videos(info, limit)
        print(f"  تعداد ویدیوهای یافت‌شده (واقعی): {len(videos)}")

        md_text = generate_markdown_for_channel(info, videos, now_iso, source_url=src_url)

        slug = slugify(info["title"])
        file_path = OUTPUT_DIR / f"{slug}__{channel_id}.md"
        file_path.write_text(md_text, encoding="utf-8")
        print(f"  فایل خروجی: {file_path}\n")

    print("پایان کار.")


if __name__ == "__main__":
    main()
