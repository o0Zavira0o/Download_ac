from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def _parse_allowed_ids(raw: str) -> set[int]:
    allowed_ids: set[int] = set()
    raw = raw.strip()
    if not raw:
        return allowed_ids
    for part in raw.split(','):
        part = part.strip()
        if part:
            allowed_ids.add(int(part))
    return allowed_ids


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    telegram_bot_token: str | None
    allowed_telegram_user_ids: set[int]
    openai_image_model: str
    portrait_size: str
    landscape_size: str
    square_size: str
    image_quality: str
    output_format: str
    min_output_bytes: int
    work_dir: Path
    output_dir: Path

    # GitHub Actions queue settings
    queue_root: Path
    queue_max_images_per_run: int
    queue_default_limit_wait_minutes: int

    @classmethod
    def from_env(cls) -> 'Settings':
        openai_api_key = os.getenv('OPENAI_API_KEY', '').strip()
        if not openai_api_key:
            raise RuntimeError('OPENAI_API_KEY is required')

        work_dir = Path(os.getenv('WORK_DIR', './work')).resolve()
        output_dir = Path(os.getenv('OUTPUT_DIR', './outputs')).resolve()
        queue_root = Path(os.getenv('QUEUE_ROOT', './image_jobs')).resolve()

        work_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        queue_root.mkdir(parents=True, exist_ok=True)

        return cls(
            openai_api_key=openai_api_key,
            telegram_bot_token=os.getenv('TELEGRAM_BOT_TOKEN', '').strip() or None,
            allowed_telegram_user_ids=_parse_allowed_ids(os.getenv('ALLOWED_TELEGRAM_USER_IDS', '')),
            openai_image_model=os.getenv('OPENAI_IMAGE_MODEL', 'gpt-image-2').strip(),
            portrait_size=os.getenv('PORTRAIT_SIZE', '1152x2048').strip(),
            landscape_size=os.getenv('LANDSCAPE_SIZE', '2048x1152').strip(),
            square_size=os.getenv('SQUARE_SIZE', '2048x2048').strip(),
            image_quality=os.getenv('IMAGE_QUALITY', 'high').strip(),
            output_format=os.getenv('OUTPUT_FORMAT', 'png').strip(),
            min_output_bytes=int(os.getenv('MIN_OUTPUT_BYTES', '2097152').strip() or '0'),
            work_dir=work_dir,
            output_dir=output_dir,
            queue_root=queue_root,
            queue_max_images_per_run=int(os.getenv('QUEUE_MAX_IMAGES_PER_RUN', '25').strip() or '25'),
            queue_default_limit_wait_minutes=int(os.getenv('QUEUE_DEFAULT_LIMIT_WAIT_MINUTES', '60').strip() or '60'),
        )
