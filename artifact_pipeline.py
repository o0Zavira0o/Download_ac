import argparse
import requests
import os
import sys
import uuid

def resolve_direct_url(youtube_url):
    """ارتباط با Cobalt API برای تبدیل لینک یوتیوب به لینک مستقیم دانلود"""
    print(f"[*] Requesting Cobalt API for: {youtube_url}")
    api_url = "https://api.cobalt.tools/api/json"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "GitHubActions-ArtifactPipeline/1.0"
    }
    payload = {
        "url": youtube_url,
        "vQuality": "720",
        "filenamePattern": "basic"
    }
    
    try:
        response = requests.post(api_url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        if data.get("status") == "error":
            print(f"[!] API Error: {data.get('text')}")
            return None
            
        return data.get("url")
    except Exception as e:
        print(f"[!] Failed to resolve URL via Cobalt: {e}")
        return None

def download_file(direct_url, output_path):
    """دانلود فایل با استفاده از لینک مستقیم به صورت Stream"""
    print(f"[*] Starting download from direct URL...")
    try:
        with requests.get(direct_url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(output_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
        print(f"[*] Download successfully saved to: {output_path}")
        return True
    except Exception as e:
        print(f"[!] Download failed: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="YouTube Artifact Ingestion Pipeline")
    parser.add_argument("--url", required=True, help="YouTube URL to process")
    args = parser.parse_args()

    # مرحله ۱: دریافت لینک مستقیم
    direct_url = resolve_direct_url(args.url)
    if not direct_url:
        print("[!] Pipeline aborted at resolution stage.")
        sys.exit(1)

    # مرحله ۲: دانلود فایل
    output_filename = f"artifact_{uuid.uuid4().hex[:8]}.mp4"
    success = download_file(direct_url, output_filename)
    
    if not success:
        print("[!] Pipeline aborted at download stage.")
        sys.exit(1)

    # مرحله ۳: در اینجا می‌توانید کدهای مربوط به فشرده‌سازی (zip) یا تکه‌تکه کردن (split) 
    # که قبلاً داشتید را قرار دهید. فایل ویدیویی اکنون به نام output_filename در دسترس است.
    print("[*] Artifact pipeline completed successfully. Ready for commit/push.")

if __name__ == "__main__":
    main()
