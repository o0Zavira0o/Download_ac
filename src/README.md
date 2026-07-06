# GitHub Actions Image Queue Worker

این پروژه برای پردازش صفی تصاویر با GitHub Actions و OpenAI Image API ساخته شده است.

## ساختار اصلی

```text
.github/workflows/image-queue.yml   # اکشن اصلی
src/queue_worker.py                 # صف، شماره گذاری، ادامه از نقطه توقف
src/image_worker.py                 # ویرایش تصویر با API رسمی
image_jobs/inbox/portrait/          # ورودی های عمودی
image_jobs/inbox/landscape/         # ورودی های افقی
image_jobs/inbox/auto/              # تشخیص خودکار جهت از ابعاد تصویر
image_jobs/outputs/                 # خروجی های نهایی
image_jobs/archive/originals/       # نسخه آرشیو از ورودی های پردازش شده
image_jobs/STATUS.md                # گزارش قابل خواندن آخرین وضعیت
image_jobs/state.json               # وضعیت دقیق صف برای ادامه کار
```

## روش استفاده سریع

1. همه فایل های این پروژه را در ریشه ریپوی GitHub خودت کپی کن.
2. در GitHub به `Settings > Secrets and variables > Actions > Secrets` برو.
3. Secret زیر را بساز:

```text
OPENAI_API_KEY
```

4. در همان صفحه، اگر خواستی از `Variables` این موارد را تنظیم کن:

```text
OPENAI_IMAGE_MODEL=gpt-image-2
PORTRAIT_SIZE=1152x2048
LANDSCAPE_SIZE=2048x1152
SQUARE_SIZE=2048x2048
IMAGE_QUALITY=high
OUTPUT_FORMAT=png
MIN_OUTPUT_BYTES=2097152
QUEUE_MAX_IMAGES_PER_RUN=25
QUEUE_DEFAULT_LIMIT_WAIT_MINUTES=60
```

5. عکس های خام را در یکی از این پوشه ها آپلود کن:

```text
image_jobs/inbox/portrait/
image_jobs/inbox/landscape/
image_jobs/inbox/auto/
```

6. Commit کن. Action خودش اجرا می شود.
7. خروجی ها را از اینجا بردار:

```text
image_jobs/outputs/
```

8. گزارش وضعیت را از اینجا بخوان:

```text
image_jobs/STATUS.md
```

## ترتیب و شماره گذاری

هر عکس جدید یک شماره ثابت می گیرد، مثل:

```text
000001
000002
000003
```

نام خروجی ها با همین شماره شروع می شود. اگر می خواهی ترتیب دقیق خودت را بدهی، فایل زیر را بساز:

```text
image_jobs/manifest.csv
```

نمونه:

```csv
filename,orientation,order,note
inbox/portrait/a.png,portrait,1,first
inbox/landscape/b.png,landscape,2,second
inbox/auto/c.png,auto,3,auto detect
```

اگر manifest نداشته باشی، فایل ها بر اساس مسیر و نام فایل مرتب می شوند.

## رفتار هنگام rate limit

اگر API خطای rate limit بدهد، اکشن صف را پاک نمی کند. وضعیت را در `image_jobs/state.json` و گزارش را در `image_jobs/STATUS.md` ذخیره می کند. اگر API زمان reset را در هدرها داده باشد، همان زمان ثبت می شود. اگر نداده باشد، مقدار `QUEUE_DEFAULT_LIMIT_WAIT_MINUTES` استفاده می شود.

اکشن هر ساعت یک بار هم با schedule اجرا می شود، بنابراین بعد از زمان انتظار دوباره ادامه می دهد.

## نکته امنیتی

کلید API را هرگز داخل فایل های پروژه نگذار. فقط از GitHub Actions Secrets استفاده کن.

اگر تصاویرت خصوصی هستند، ریپو را Private کن؛ چون در حالت عادی فایل هایی که در ریپوی Public commit می شوند برای دیگران قابل مشاهده هستند.

## محدودیت مهم

این پروژه فقط با OpenAI API رسمی کار می کند. برای ورود خودکار به ChatGPT وب، Switch account، کوکی، پسورد، یا چرخاندن حساب ها طراحی نشده است.
