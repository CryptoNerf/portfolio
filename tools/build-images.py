#!/usr/bin/env python3
"""
Генератор производных версий изображений для портфолио.

Для каждой картинки, на которую ссылается HTML, создаёт три версии:

  source/img/low/<имя как у оригинала>  ~64px  — мгновенный размытый плейсхолдер (1-3 КБ)
  source/img/thumb/<имя>.webp           ~400px — для мелких ячеек сеток (колода карт)
  source/img/opt/<имя>.webp            ~1600px — то, что реально показывается на странице

Анимации (GIF и APNG) весят десятки мегабайт, поэтому вместо картинок из них
собирается mp4 (в 20-100 раз легче) плюс статичный постер.

Имя файла в low/ намеренно совпадает с именем оригинала (включая расширение),
чтобы путь всегда получался механической заменой и ссылки не «разъезжались».

Запуск:  python3 tools/build-images.py [--force]
"""

import io
import os
import re
import glob
import shutil
import subprocess
import sys

from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG = os.path.join(ROOT, "source", "img")
LOW, THUMB, OPT = (os.path.join(IMG, d) for d in ("low", "thumb", "opt"))

LOW_EDGE = 64
THUMB_EDGE = 400
OPT_EDGE = 1600
# Ячейка галереи шире 540 CSS-px не бывает (сетка auto-fill по 250px),
# так что 1600 px хватает даже при тройной плотности пикселей.
OPT_QUALITY = 90
THUMB_QUALITY = 80
RASTER = (".png", ".jpg", ".jpeg", ".gif", ".webp")
VIDEO = (".mp4", ".webm", ".mov")

FORCE = "--force" in sys.argv


def originals_by_stem():
    """Оригиналы, разложенные по имени без расширения."""
    table = {}
    for folder in (IMG, os.path.join(IMG, "web")):
        for path in glob.glob(os.path.join(folder, "*")):
            if os.path.isfile(path) and path.lower().endswith(RASTER):
                table.setdefault(os.path.splitext(os.path.basename(path))[0], path)
    return table


def collect_sources():
    """Оригиналы всех картинок, которые так или иначе показываются на страницах.

    Разметка ссылается сразу на несколько версий одной картинки (low/, opt/,
    thumb/, оригинал), поэтому ссылку сводим к имени без расширения и по нему
    находим исходник. Иначе после перевода страниц на opt/ скрипт перестал бы
    видеть собственные исходные файлы.
    """
    table = originals_by_stem()
    found = set()
    pages = [os.path.join(ROOT, "index.html")] + sorted(
        glob.glob(os.path.join(ROOT, "html", "*.html"))
    )
    for page in pages:
        html = open(page, encoding="utf-8").read()
        for ref in re.findall(
            r'(?:src|data-src|data-full|data-fallback|poster)\s*=\s*"([^"]+)"', html
        ):
            if not ref or ref.startswith(("http", "data:")):
                continue
            if not ref.lower().endswith(RASTER):
                continue
            original = table.get(os.path.splitext(os.path.basename(ref))[0])
            if original:
                found.add(original)
    # превью в web.html подставляются скриптом, в разметке их путей нет
    for path in glob.glob(os.path.join(IMG, "web", "*")):
        if path.lower().endswith(RASTER):
            found.add(path)
    return sorted(found)


def collect_thumb_stems():
    """thumb/ нужен только там, где на него есть ссылка (сейчас — колода карт).

    Без этой проверки папка заполнялась версиями, которые никто не открывает,
    и в репозиторий уезжали десятки лишних файлов.
    """
    stems = set()
    pages = [os.path.join(ROOT, "index.html")] + sorted(
        glob.glob(os.path.join(ROOT, "html", "*.html"))
    )
    for page in pages:
        html = open(page, encoding="utf-8").read()
        for ref in re.findall(r'"[^"]*/thumb/([^"]+)\.webp"', html):
            stems.add(os.path.basename(ref))
    return stems


def collect_videos():
    """Видео, встроенные в страницы: им нужен постер, иначе до старта пустота."""
    found = set()
    pages = [os.path.join(ROOT, "index.html")] + sorted(
        glob.glob(os.path.join(ROOT, "html", "*.html"))
    )
    for page in pages:
        base = os.path.dirname(page)
        html = open(page, encoding="utf-8").read()
        for ref in re.findall(r'(?:src|data-src)\s*=\s*"([^"]+)"', html):
            if not ref or ref.startswith(("http", "data:")):
                continue
            path = os.path.normpath(os.path.join(base, ref))
            # opt/ — это результат работы скрипта, постеры нужны только исходникам
            if os.path.dirname(path) != IMG:
                continue
            if path.lower().endswith(VIDEO) and os.path.exists(path):
                found.add(path)
    return sorted(found)


def build_poster(src):
    """Первый кадр видео -> крошечный jpg, он же poster у <video>."""
    dst = out_path(LOW, src, ".jpg")
    if fresh(src, dst):
        return dst, False
    ffmpeg(["-i", src, "-frames:v", "1",
            "-vf", f"scale={LOW_EDGE}:-2:flags=lanczos", "-q:v", "8", dst])
    return dst, True


def is_animated(im):
    return getattr(im, "n_frames", 1) > 1


def fresh(src, dst):
    """Пересобирать только если производная старше оригинала."""
    return (
        not FORCE
        and os.path.exists(dst)
        and os.path.getmtime(dst) > os.path.getmtime(src)
    )


def out_path(root, src, ext=None):
    """web/-превью держим в подпапке, чтобы имена не пересекались с общими."""
    rel = os.path.relpath(os.path.dirname(src), IMG)
    stem = os.path.splitext(os.path.basename(src))[0]
    name = stem + ext if ext else os.path.basename(src)
    out = os.path.join(root, name) if rel == "." else os.path.join(root, rel, name)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    return out


def save_low(im, src, dst):
    """Плейсхолдер в формате оригинала: PNG квантуется до 64 цветов, JPEG жмётся."""
    small = im.copy()
    small.thumbnail((LOW_EDGE, LOW_EDGE), Image.LANCZOS)
    ext = os.path.splitext(dst)[1].lower()
    if ext in (".jpg", ".jpeg"):
        small.convert("RGB").save(dst, "JPEG", quality=42, optimize=True)
    elif ext == ".webp":
        small.save(dst, "WEBP", quality=45, method=6)
    elif ext == ".gif":
        small.convert("RGB").convert(
            "P", palette=Image.ADAPTIVE, colors=64
        ).save(dst, "GIF", optimize=True)
    else:
        mode = "RGBA" if small.mode in ("RGBA", "LA", "P") else "RGB"
        small.convert(mode).quantize(colors=64, method=Image.FASTOCTREE).save(
            dst, "PNG", optimize=True
        )


def save_webp(im, dst, edge, quality):
    big = im.copy()
    big.thumbnail((edge, edge), Image.LANCZOS)
    if big.mode not in ("RGB", "RGBA"):
        big = big.convert("RGBA" if "A" in big.mode or big.mode == "P" else "RGB")
    big.save(dst, "WEBP", quality=quality, method=6)


def ffmpeg(args):
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", *args], check=True, cwd=ROOT
    )


def convert_animation(src):
    """GIF/APNG -> mp4. Высота выравнивается до чётной, иначе h264 не соберётся.

    Только h264/yuv420p: он играет во всех браузерах и декодируется железом,
    поэтому второй вариант в webm был бы мёртвым весом в репозитории.
    """
    mp4 = out_path(OPT, src, ".mp4")
    if fresh(src, mp4):
        return mp4, False
    ffmpeg(["-i", src, "-vf", f"scale='min({OPT_EDGE},iw)':-2:flags=lanczos",
            "-c:v", "libx264", "-crf", "26", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", "-an", mp4])
    return mp4, True


def human(n):
    return f"{n / 1024 / 1024:.2f} MB" if n >= 1024 * 1024 else f"{n / 1024:.0f} KB"


def main():
    sources = collect_sources()
    thumb_stems = collect_thumb_stems()
    print(f"Найдено исходных картинок: {len(sources)}")
    print(f"Из них нужны миниатюры thumb/: {len(thumb_stems)}\n")
    before = after = 0
    built = 0

    for src in sources:
        rel = os.path.relpath(src, ROOT)
        try:
            raw = Image.open(src)
            animated = is_animated(raw)
            im = raw if animated else ImageOps.exif_transpose(raw)
        except Exception as exc:
            print(f"  ПРОПУСК {rel}: {exc}")
            continue

        src_size = os.path.getsize(src)
        before += src_size

        if animated:
            mp4, did = convert_animation(src)
            raw.seek(0)
            frame = raw.convert("RGBA")
            opt_poster = out_path(OPT, src, ".webp")
            if not fresh(src, opt_poster):
                save_webp(frame, opt_poster, OPT_EDGE, OPT_QUALITY)
            if os.path.splitext(os.path.basename(src))[0] in thumb_stems:
                thumb_poster = out_path(THUMB, src, ".webp")
                if not fresh(src, thumb_poster):
                    save_webp(frame, thumb_poster, THUMB_EDGE, THUMB_QUALITY)
            low = out_path(LOW, src)
            if not fresh(src, low):
                save_low(frame, src, low)
            after += os.path.getsize(mp4)
            built += did
            print(f"  анимация {rel}: {human(src_size)} -> mp4 {human(os.path.getsize(mp4))}")
            continue

        stem = os.path.splitext(os.path.basename(src))[0]
        low = out_path(LOW, src)
        opt = out_path(OPT, src, ".webp")
        if not fresh(src, low):
            save_low(im, src, low)
            built += 1
        if stem in thumb_stems:
            thumb = out_path(THUMB, src, ".webp")
            if not fresh(src, thumb):
                save_webp(im, thumb, THUMB_EDGE, THUMB_QUALITY)
        if not fresh(src, opt):
            save_webp(im, opt, OPT_EDGE, OPT_QUALITY)
        after += os.path.getsize(opt)

    for src in collect_videos():
        try:
            dst, did = build_poster(src)
        except Exception as exc:
            print(f"  ПОСТЕР не собран {os.path.relpath(src, ROOT)}: {exc}")
            continue
        built += did
        if did:
            print(f"  постер {os.path.relpath(dst, ROOT)} "
                  f"({os.path.getsize(dst) / 1024:.1f} КБ)")

    print(f"\nПересобрано файлов: {built}")
    print(f"Оригиналы:            {human(before)}")
    print(f"Версии для показа:    {human(after)}")
    print(f"Экономия трафика:     {(1 - after / before) * 100:.0f}%")


if __name__ == "__main__":
    main()
