import os
import re
import json
import requests
import feedparser
from datetime import datetime

DB_FILE = "db.json"

SOURCES = {
    "foreign_affairs": {
        "name": "Foreign Affairs",
        "rss": "https://www.foreignaffairs.com/rss.xml",
        "folder": "articles/foreign_affairs"
    },
    "foreign_policy": {
        "name": "Foreign Policy",
        "rss": "https://foreignpolicy.com/feed/",
        "folder": "articles/foreign_policy"
    },
    "new_yorker_magazine": {
        "name": "New Yorker (Magazine)",
        "rss": "https://www.newyorker.com/feed/rss",
        "folder": "articles/new_yorker_magazine"
    }
}


def slugify(text):
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'\s+', '-', text)
    return text[:80]


def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f:
            return json.load(f)
    return {}


def save_db(db):
    with open(DB_FILE, "w") as f:
        json.dump(db, f, indent=2)


def search_archive(url):
    try:
        r = requests.get("https://archive.ph/" + url, timeout=10)
        if r.status_code == 200:
            return r.url
    except:
        pass
    return None


def create_markdown(article, source_info, archive_url):
    os.makedirs(source_info["folder"], exist_ok=True)

    filename = slugify(article.title) + ".md"
    path = os.path.join(source_info["folder"], filename)

    published = getattr(article, "published", "Unknown")

    content = f"""# {article.title}

**Source:** {source_info["name"]}  
**Published:** {published}  
**Original URL:** {article.link}  
**Archive URL:** {archive_url or "Not found"}  

---

## Summary (from RSS)

{getattr(article, "summary", "No summary available.")}

---

> For full content, visit the original source.
"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def main():
    db = load_db()

    for key, source in SOURCES.items():
        feed = feedparser.parse(source["rss"])

        for entry in feed.entries:
            if entry.link in db:
                continue

            archive_url = search_archive(entry.link)

            create_markdown(entry, source, archive_url)

            db[entry.link] = {
                "title": entry.title,
                "date_added": str(datetime.utcnow())
            }

            print(f"Added: {entry.title}")

    save_db(db)


if __name__ == "__main__":
    main()
