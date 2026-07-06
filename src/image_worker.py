from __future__ import annotations

import base64
import mimetypes
import re
import time
from pathlib import Path
from typing import Literal

from openai import APIConnectionError, APIStatusError, OpenAI, RateLimitError
from PIL import Image, ImageOps, PngImagePlugin
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from prompts import build_prompt
from settings import Settings

Orientation = Literal['portrait', 'landscape', 'square']

ORIENTATION_WORDS = {
    'portrait': ['portrait', 'vertical', 'عمودی', 'عمودي'],
    'landscape': ['landscape', 'horizontal', 'افقی', 'افقي'],
    'square': ['square', 'مربعی', 'مربعي'],
}


def infer_orientation(text: str | None, image_path: Path) -> Orientation:
    text_norm = (text or '').strip().lower()
    for orientation, words in ORIENTATION_WORDS.items():
        if any(word in text_norm for word in words):
            return orientation  # type: ignore[return-value]

    with Image.open(image_path) as img:
        width, height = img.size
    if height > width:
        return 'portrait'
    if width > height:
        return 'landscape'
    return 'square'


def target_size(settings: Settings, orientation: Orientation) -> str:
    if orientation == 'portrait':
        return settings.portrait_size
    if orientation == 'landscape':
        return settings.landscape_size
    return settings.square_size


def normalize_image(input_path: Path, work_dir: Path) -> Path:
    '''Convert input to PNG and fix EXIF orientation for safer API upload.'''
    work_dir.mkdir(parents=True, exist_ok=True)
    output_path = work_dir / f'normalized_{input_path.stem}_{int(time.time() * 1000)}.png'
    with Image.open(input_path) as img:
        img = ImageOps.exif_transpose(img).convert('RGBA')
        img.save(output_path, format='PNG')
    return output_path


def ensure_min_png_size(output_path: Path, min_bytes: int) -> None:
    '''Preserve pixels while avoiding tiny PNG outputs.

    If a PNG is smaller than the requested minimum, re-save it with no PNG
    compression. This can increase file size without changing pixels. It does
    not create extra real detail; it only prevents overly compressed tiny files.
    '''
    if min_bytes <= 0 or output_path.suffix.lower() != '.png':
        return
    if output_path.stat().st_size >= min_bytes:
        return

    with Image.open(output_path) as img:
        img = img.convert('RGBA')
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text('note', 'Saved as low-compression PNG for full-quality file delivery.')
        img.save(output_path, format='PNG', compress_level=0, optimize=False, pnginfo=metadata)


def safe_slug(name: str) -> str:
    stem = Path(name).stem or 'image'
    stem = re.sub(r'[^a-zA-Z0-9._-]+', '-', stem).strip('-._')
    return stem[:80] or 'image'


def _retryable_exception(exc: BaseException) -> bool:
    if isinstance(exc, RateLimitError):
        return False
    if isinstance(exc, APIConnectionError):
        return True
    if isinstance(exc, APIStatusError):
        status_code = getattr(exc, 'status_code', None)
        return status_code is None or status_code >= 500
    return False


class ImageWorker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = OpenAI(api_key=settings.openai_api_key)

    @retry(
        retry=retry_if_exception(_retryable_exception),
        wait=wait_exponential_jitter(initial=2, max=60),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _call_image_edit(self, image_path: Path, prompt: str, size: str):
        with image_path.open('rb') as image_file:
            return self.client.images.edit(
                model=self.settings.openai_image_model,
                image=[image_file],
                prompt=prompt,
                size=size,
                quality=self.settings.image_quality,
                output_format=self.settings.output_format,
            )

    def process(self, input_path: Path, orientation_hint: str | None = None, output_name_prefix: str | None = None) -> Path:
        input_path = input_path.resolve()
        if not input_path.exists():
            raise FileNotFoundError(input_path)

        normalized = normalize_image(input_path, self.settings.work_dir)
        orientation = infer_orientation(orientation_hint, normalized)
        size = target_size(self.settings, orientation)
        prompt = build_prompt(orientation)

        result = self._call_image_edit(normalized, prompt, size)
        image_base64 = result.data[0].b64_json
        image_bytes = base64.b64decode(image_base64)

        output_ext = self.settings.output_format.lower().replace('jpeg', 'jpg')
        prefix = f'{output_name_prefix}__' if output_name_prefix else ''
        out_name = f'{prefix}{safe_slug(input_path.name)}__{orientation}__{size.replace("x", "-")}.{output_ext}'
        output_path = self.settings.output_dir / out_name
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(image_bytes)
        ensure_min_png_size(output_path, self.settings.min_output_bytes)
        return output_path
