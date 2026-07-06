from __future__ import annotations

import argparse
import csv
import email.utils
import hashlib
from dataclasses import replace
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from openai import RateLimitError

from image_worker import ImageWorker, safe_slug
from settings import Settings

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp'}
ORIENTATION_ALIASES = {
    'portrait': 'portrait',
    'vertical': 'portrait',
    'عمودی': 'portrait',
    'عمودي': 'portrait',
    'p': 'portrait',
    'landscape': 'landscape',
    'horizontal': 'landscape',
    'افقی': 'landscape',
    'افقي': 'landscape',
    'l': 'landscape',
    'square': 'square',
    'مربعی': 'square',
    'مربعي': 'square',
    's': 'square',
    'auto': 'auto',
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)
    except ValueError:
        return None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def normalize_orientation(value: str | None) -> str | None:
    if not value:
        return None
    key = value.strip().lower()
    return ORIENTATION_ALIASES.get(key)


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        backup = path.with_suffix(path.suffix + f'.broken-{int(utc_now().timestamp())}')
        shutil.copy2(path, backup)
        return default


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def load_manifest(queue_root: Path) -> dict[str, dict[str, Any]]:
    manifest_path = queue_root / 'manifest.csv'
    if not manifest_path.exists():
        return {}
    rows: dict[str, dict[str, Any]] = {}
    with manifest_path.open('r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            filename = (row.get('filename') or row.get('path') or '').strip()
            if not filename:
                continue
            rows[filename.replace('\\', '/')] = {
                'orientation': normalize_orientation(row.get('orientation')) or row.get('orientation') or '',
                'order': _int_or_none(row.get('order')),
                'note': row.get('note') or '',
            }
    return rows


def _int_or_none(value: str | None) -> int | None:
    if value is None or not str(value).strip():
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def orientation_from_path(rel_path: str, manifest: dict[str, dict[str, Any]]) -> str | None:
    rel = rel_path.replace('\\', '/')
    name = Path(rel).name
    manifest_row = manifest.get(rel) or manifest.get(name)
    if manifest_row:
        orientation = normalize_orientation(str(manifest_row.get('orientation') or ''))
        if orientation and orientation != 'auto':
            return orientation

    parts = Path(rel).parts
    for part in parts[:-1]:
        orientation = normalize_orientation(part)
        if orientation and orientation != 'auto':
            return orientation

    lowered = name.lower()
    prefixes = {
        'portrait_': 'portrait', 'portrait-': 'portrait', 'p_': 'portrait', 'p-': 'portrait',
        'vertical_': 'portrait', 'vertical-': 'portrait',
        'landscape_': 'landscape', 'landscape-': 'landscape', 'l_': 'landscape', 'l-': 'landscape',
        'horizontal_': 'landscape', 'horizontal-': 'landscape',
        'square_': 'square', 'square-': 'square', 's_': 'square', 's-': 'square',
    }
    for prefix, orientation in prefixes.items():
        if lowered.startswith(prefix):
            return orientation
    return None


def order_for(rel_path: str, manifest: dict[str, dict[str, Any]]) -> tuple[int, str]:
    rel = rel_path.replace('\\', '/')
    name = Path(rel).name
    row = manifest.get(rel) or manifest.get(name) or {}
    order = row.get('order')
    if isinstance(order, int):
        return order, rel.lower()
    return 999_999_999, rel.lower()


def ensure_dirs(queue_root: Path) -> None:
    for sub in [
        'inbox/portrait',
        'inbox/landscape',
        'inbox/auto',
        'archive/originals',
        'outputs',
        'reports',
    ]:
        (queue_root / sub).mkdir(parents=True, exist_ok=True)
        keep = queue_root / sub / '.gitkeep'
        if not keep.exists():
            keep.write_text('', encoding='utf-8')


def scan_new_images(queue_root: Path, state: dict[str, Any]) -> int:
    ensure_dirs(queue_root)
    manifest = load_manifest(queue_root)
    inbox = queue_root / 'inbox'
    existing_keys = {item['key'] for item in state.get('items', [])}
    candidates: list[tuple[tuple[int, str], Path, str, str, str | None]] = []

    for path in inbox.rglob('*'):
        if not path.is_file() or path.name.startswith('.'):
            continue
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        rel = path.relative_to(queue_root).as_posix()
        digest = sha256_file(path)
        key = f'{rel}|{digest[:24]}'
        if key in existing_keys:
            continue
        orientation = orientation_from_path(rel, manifest)
        candidates.append((order_for(rel, manifest), path, rel, digest, orientation))

    candidates.sort(key=lambda item: item[0])
    next_id = int(state.get('next_id', 1))
    added = 0
    for _, path, rel, digest, orientation in candidates:
        item_id = next_id
        next_id += 1
        state.setdefault('items', []).append({
            'id': item_id,
            'key': f'{rel}|{digest[:24]}',
            'input_path': rel,
            'input_sha256': digest,
            'original_name': path.name,
            'orientation': orientation or 'auto',
            'status': 'pending',
            'attempts': 0,
            'added_at': iso(utc_now()),
            'updated_at': iso(utc_now()),
            'output_path': '',
            'archive_path': '',
            'last_error': '',
        })
        added += 1
    state['next_id'] = next_id
    return added


def parse_retry_header(value: str | None, now: datetime) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    # Retry-After can be seconds or an HTTP-date.
    try:
        return now + timedelta(seconds=max(0, float(value)))
    except ValueError:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def parse_duration_like(value: str | None, now: datetime) -> datetime | None:
    if not value:
        return None
    text = value.strip().lower()
    if not text:
        return None
    try:
        # Some APIs use Unix epoch seconds.
        if text.replace('.', '', 1).isdigit():
            number = float(text)
            if number > 1_000_000_000:
                return datetime.fromtimestamp(number, timezone.utc)
            return now + timedelta(seconds=number)
    except ValueError:
        pass
    units = [
        ('ms', 0.001),
        ('s', 1),
        ('sec', 1),
        ('m', 60),
        ('min', 60),
        ('h', 3600),
    ]
    for suffix, multiplier in units:
        if text.endswith(suffix):
            try:
                number = float(text[: -len(suffix)].strip())
                return now + timedelta(seconds=max(0, number * multiplier))
            except ValueError:
                return None
    return None


def estimate_limit_reset(exc: RateLimitError, default_wait_minutes: int) -> tuple[datetime, str]:
    now = utc_now()
    response = getattr(exc, 'response', None)
    headers = getattr(response, 'headers', {}) or {}

    candidates: list[tuple[str, datetime]] = []
    for name in ['retry-after', 'Retry-After', 'retry-after-ms', 'Retry-After-Ms']:
        dt = parse_retry_header(headers.get(name), now) or parse_duration_like(headers.get(name), now)
        if dt:
            candidates.append((name, dt))

    for name in [
        'x-ratelimit-reset-requests',
        'x-ratelimit-reset-tokens',
        'x-ratelimit-reset-images',
        'x-request-limit-reset',
    ]:
        dt = parse_duration_like(headers.get(name), now)
        if dt:
            candidates.append((name, dt))

    if candidates:
        # Pick the furthest reset among reported windows so the next run is safer.
        source, dt = max(candidates, key=lambda item: item[1])
        return dt, f'API header {source}'

    fallback = now + timedelta(minutes=max(1, default_wait_minutes))
    return fallback, f'fallback {default_wait_minutes} minutes; API did not expose a reset time in headers'


def short_error(exc: BaseException, max_len: int = 900) -> str:
    text = f'{type(exc).__name__}: {exc}'
    text = text.replace('\n', ' ').strip()
    return text[:max_len]


def item_sort_key(item: dict[str, Any]) -> int:
    return int(item.get('id', 0))


def archive_input(queue_root: Path, item: dict[str, Any], source: Path) -> str:
    archive_dir = queue_root / 'archive' / 'originals'
    archive_dir.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower()
    archive_name = f"{int(item['id']):06d}__{safe_slug(source.name)}{suffix}"
    dest = archive_dir / archive_name
    if source.exists():
        shutil.move(str(source), str(dest))
    return dest.relative_to(queue_root).as_posix()


def write_status_report(queue_root: Path, state: dict[str, Any], added: int, message: str) -> None:
    items = sorted(state.get('items', []), key=item_sort_key)
    total = len(items)
    processed = sum(1 for item in items if item.get('status') == 'done')
    pending = sum(1 for item in items if item.get('status') == 'pending')
    failed = sum(1 for item in items if item.get('status') == 'failed')
    blocked_until = state.get('blocked_until') or ''
    blocked_item_id = state.get('blocked_item_id') or ''

    lines = [
        '# Image Queue Status',
        '',
        f'- Last update UTC: `{iso(utc_now())}`',
        f'- New files added this run: `{added}`',
        f'- Total queued items: `{total}`',
        f'- Done: `{processed}`',
        f'- Pending: `{pending}`',
        f'- Failed: `{failed}`',
        f'- Message: {message}',
    ]
    if blocked_until:
        lines.append(f'- Blocked until UTC: `{blocked_until}`')
    if blocked_item_id:
        lines.append(f'- Could not continue from image number: `{blocked_item_id}`')
    lines.extend(['', '## Latest items', ''])
    lines.append('| id | status | orientation | input | output | last error |')
    lines.append('|---:|---|---|---|---|---|')
    for item in items[-30:]:
        lines.append(
            '| {id} | {status} | {orientation} | `{input_path}` | `{output_path}` | {last_error} |'.format(
                id=item.get('id', ''),
                status=item.get('status', ''),
                orientation=item.get('orientation', ''),
                input_path=item.get('input_path', ''),
                output_path=item.get('output_path', ''),
                last_error=(item.get('last_error') or '').replace('|', '/'),
            )
        )
    (queue_root / 'STATUS.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def process_queue(settings: Settings, max_images: int | None = None, force: bool = False) -> int:
    queue_root = settings.queue_root
    ensure_dirs(queue_root)
    state_path = queue_root / 'state.json'
    default_state: dict[str, Any] = {
        'version': 1,
        'next_id': 1,
        'blocked_until': '',
        'blocked_item_id': '',
        'blocked_reason': '',
        'items': [],
    }
    state = load_json(state_path, default_state)
    added = scan_new_images(queue_root, state)

    blocked_until = parse_iso(state.get('blocked_until'))
    if blocked_until and blocked_until > utc_now() and not force:
        message = f'Queue is waiting for API limit reset until {iso(blocked_until)}.'
        write_json(state_path, state)
        write_status_report(queue_root, state, added, message)
        print(message)
        return 0

    state['blocked_until'] = ''
    state['blocked_item_id'] = ''
    state['blocked_reason'] = ''

    limit = max_images if max_images is not None else settings.queue_max_images_per_run
    # Queue outputs must land under image_jobs/outputs, not the old ./outputs folder.
    queue_output_dir = (queue_root / 'outputs').resolve()
    queue_output_dir.mkdir(parents=True, exist_ok=True)
    worker_settings = replace(settings, output_dir=queue_output_dir)
    worker = ImageWorker(worker_settings)

    processed_this_run = 0
    message = 'Queue checked; no pending images.'

    for item in sorted(state.get('items', []), key=item_sort_key):
        if item.get('status') != 'pending':
            continue
        if processed_this_run >= limit:
            message = f'Max images per run reached ({limit}); remaining images will continue next run.'
            break

        rel_input = item.get('input_path') or ''
        input_path = queue_root / rel_input
        if not input_path.exists():
            item['status'] = 'failed'
            item['last_error'] = 'Input file is missing from inbox and was not processed.'
            item['updated_at'] = iso(utc_now())
            message = 'Some queued files were missing.'
            continue

        item['attempts'] = int(item.get('attempts', 0)) + 1
        item['updated_at'] = iso(utc_now())
        orientation = item.get('orientation')
        orientation_hint = None if orientation == 'auto' else str(orientation)
        id_prefix = f'{int(item["id"]):06d}'
        print(f'Processing #{id_prefix}: {rel_input} orientation={orientation or "auto"}')
        try:
            output_path = worker.process(input_path, orientation_hint=orientation_hint, output_name_prefix=id_prefix)
            item['status'] = 'done'
            item['output_path'] = output_path.relative_to(queue_root).as_posix()
            item['archive_path'] = archive_input(queue_root, item, input_path)
            item['last_error'] = ''
            item['completed_at'] = iso(utc_now())
            processed_this_run += 1
            message = f'Processed {processed_this_run} image(s) this run.'
        except RateLimitError as exc:
            reset_at, reset_reason = estimate_limit_reset(exc, settings.queue_default_limit_wait_minutes)
            item['status'] = 'pending'
            item['last_error'] = short_error(exc)
            item['updated_at'] = iso(utc_now())
            state['blocked_until'] = iso(reset_at)
            state['blocked_item_id'] = item.get('id')
            state['blocked_reason'] = reset_reason
            message = f'API limit reached at image #{item.get("id")}. Next attempt after {iso(reset_at)} ({reset_reason}).'
            print(message)
            break
        except Exception as exc:  # noqa: BLE001
            item['status'] = 'failed'
            item['last_error'] = short_error(exc)
            item['updated_at'] = iso(utc_now())
            message = f'Image #{item.get("id")} failed; queue continued.'
            print(message)
            continue

    state['last_run_at'] = iso(utc_now())
    write_json(state_path, state)
    write_status_report(queue_root, state, added, message)
    print(message)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description='Persistent GitHub Actions image queue worker')
    parser.add_argument('--max-images', type=int, default=None, help='Maximum images to process in this run')
    parser.add_argument('--force', action='store_true', help='Ignore blocked_until and try now')
    args = parser.parse_args()

    settings = Settings.from_env()
    raise_code = process_queue(settings, max_images=args.max_images, force=args.force)
    sys.exit(raise_code)


if __name__ == '__main__':
    main()
