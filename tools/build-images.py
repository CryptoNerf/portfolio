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
import json
import math
import os
import re
import glob
import hashlib
import shutil
import subprocess
import sys
import unicodedata

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


def flatten(im, color=(0, 0, 0)):
    """Убрать альфу, положив картинку на чёрное.

    convert("RGB") просто выбрасывает альфа-канал, из-за чего прозрачный фон
    становился белым. Галерея у нас чёрная, поэтому подкладываем чёрный —
    так же, как при сборке mp4 из анимаций. Промо показываются на белой
    странице web, им передаётся белый.
    """
    if im.mode not in ("RGBA", "LA") and "transparency" not in im.info:
        return im.convert("RGB")
    rgba = im.convert("RGBA")
    backdrop = Image.new("RGBA", rgba.size, color + (255,))
    return Image.alpha_composite(backdrop, rgba).convert("RGB")


def save_low(im, src, dst):
    """Плейсхолдер в формате оригинала: PNG квантуется до 64 цветов, JPEG жмётся."""
    small = im.copy()
    small.thumbnail((LOW_EDGE, LOW_EDGE), Image.LANCZOS)
    ext = os.path.splitext(dst)[1].lower()
    if ext in (".jpg", ".jpeg"):
        flatten(small).save(dst, "JPEG", quality=42, optimize=True)
    elif ext == ".webp":
        small.save(dst, "WEBP", quality=45, method=6)
    elif ext == ".gif":
        flatten(small).convert(
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


def frame_delays(im):
    """Задержки кадров анимации в миллисекундах."""
    delays = []
    for i in range(im.n_frames):
        im.seek(i)
        delays.append(int(im.info.get("duration", 100)) or 100)
    im.seek(0)
    return delays


def animation_fps(delays):
    """Частота, на которой задержки укладываются без дробей.

    У o.gif кадры идут по 60 и 200 мс — оба делятся на 20 мс, то есть 50 fps
    воспроизводит анимацию с исходным ритмом. Выше 50 не поднимаемся: файл
    растёт, а глазу это уже не нужно.
    """
    step = 0
    for d in delays:
        step = math.gcd(step, d)
    return max(1, min(50, round(1000 / step))) if step else 25


def convert_animation(src, size, delays):
    """GIF/APNG -> mp4 поверх чёрного фона.

    Просто перевести в yuv420p нельзя: альфа-канал при этом отбрасывается, и
    прозрачный фон o.gif становился белым, а мягкое свечение html-garden
    обрезалось жёстким кругом. Поэтому кадры кладём на чёрную подложку —
    страница галереи тоже чёрная, так что результат совпадает с исходным видом.
    """
    mp4 = out_path(OPT, src, ".mp4")
    if fresh(src, mp4):
        return mp4, False
    width, height = size
    fps = animation_fps(delays)
    chain = (
        f"color=c=black:s={width}x{height}:r={fps},format=yuv420p[bg];"
        f"[0:v]format=yuva420p,fps={fps}[fg];"
        f"[bg][fg]overlay=shortest=1,"
        f"scale='min({OPT_EDGE},iw)':-2:flags=lanczos,format=yuv420p[v]"
    )
    ffmpeg(["-i", src, "-filter_complex", chain, "-map", "[v]",
            "-c:v", "libx264", "-crf", "26", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", "-an", mp4])
    return mp4, True


def human(n):
    return f"{n / 1024 / 1024:.2f} MB" if n >= 1024 * 1024 else f"{n / 1024:.0f} KB"


# --- Промо-материалы веб-проектов -------------------------------------------
#
# promo-web/<Проект>/ и promo-project/<Проект>/ — оригиналы для разделов web
# и project, как их выгрузили из макетов и монтажки. На страницах они не
# перечисляются руками: скрипт собирает версии в source/img/promo/<Проект>/
# и пишет их список в js/promo-data.js, а страница ссылается на проект
# атрибутом data-promo="<Проект>". Имена папок проектов не должны совпадать
# между promo-web/ и promo-project/.
#
# Порядок в ленте задаёт promo-web/<Проект>/order.txt — по имени файла на
# строку. Переименовывать файлы ради порядка нельзя: имя файла показывается
# подписью под материалом. Файлы, которых нет в order.txt, встают в конец
# по имени (2 раньше 10); без order.txt по имени идёт весь проект.

PROMO_SOURCES = [os.path.join(ROOT, d) for d in ("promo-web", "promo-project")]
PROMO_OUT = os.path.join(IMG, "promo")
PROMO_DATA = os.path.join(ROOT, "js", "promo-data.js")
# Лента промо — 280 CSS-px в высоту, миниатюры берём вдвое выше под ретину.
PROMO_THUMB_HEIGHT = 560
PROMO_VIDEO_EDGE = 1280
# Превью для плитки: крутится без звука, поэтому хватает начала ролика
# в небольшом размере. Полный ролик качается только в просмотре.
PROMO_PREVIEW_HEIGHT = 360
PROMO_PREVIEW_SECONDS = 12
WHITE = (255, 255, 255)


PROMO_ORDER = "order.txt"


def natural_key(name):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def promo_path(project, kind, src, ext):
    # Имя в обычной форме Unicode: git на маке коммитит именно её, и ссылка
    # с разложенной «ё» на GitHub Pages вела бы в пустоту.
    stem = os.path.splitext(display_name(src))[0]
    out = os.path.join(PROMO_OUT, project, kind, stem + ext)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    return out


def web_path(path):
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


def probe_video(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:format=duration",
         "-of", "default=noprint_wrappers=1", path],
        check=True, capture_output=True, text=True,
    ).stdout
    info = dict(line.split("=", 1) for line in out.split() if "=" in line)
    return int(info["width"]), int(info["height"]), float(info.get("duration", 0))


def build_promo_image(project, src):
    im = flatten(ImageOps.exif_transpose(Image.open(src)), WHITE)
    low = promo_path(project, "low", src, ".jpg")
    thumb = promo_path(project, "thumb", src, ".webp")
    full = promo_path(project, "opt", src, ".webp")
    if not fresh(src, low):
        small = im.copy()
        small.thumbnail((LOW_EDGE, LOW_EDGE), Image.LANCZOS)
        small.save(low, "JPEG", quality=42, optimize=True)
    if not fresh(src, thumb):
        strip = im.copy()
        strip.thumbnail((OPT_EDGE, PROMO_THUMB_HEIGHT), Image.LANCZOS)
        strip.save(thumb, "WEBP", quality=THUMB_QUALITY, method=6)
    if not fresh(src, full):
        save_webp(im, full, OPT_EDGE, OPT_QUALITY)
    return {
        "type": "img", "name": display_name(src), "w": im.width, "h": im.height,
        "low": web_path(low), "thumb": web_path(thumb), "full": web_path(full),
    }, os.path.getsize(thumb) + os.path.getsize(full)


def build_promo_video(project, src):
    """Ролик ужимаем до 1280 px и ставим moov в начало, чтобы старт был сразу.

    Для плитки отдельно собирается лёгкое превью: без него автозапуск в
    ленте качал полный ролик со звуком — до 4 МБ на плитку высотой 280px.

    Постер берём не с первого кадра — у промо он часто чёрный (затемнение).
    """
    mp4 = promo_path(project, "opt", src, ".mp4")
    preview = promo_path(project, "thumb", src, ".mp4")
    poster = promo_path(project, "thumb", src, ".jpg")
    if not fresh(src, mp4):
        ffmpeg(["-i", src,
                "-vf", f"scale='min({PROMO_VIDEO_EDGE},iw)':-2:flags=lanczos",
                "-c:v", "libx264", "-crf", "26", "-preset", "slow",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
                "-movflags", "+faststart", mp4])
    if not fresh(src, preview):
        ffmpeg(["-i", mp4, "-t", str(PROMO_PREVIEW_SECONDS),
                "-vf", f"scale=-2:'min({PROMO_PREVIEW_HEIGHT},ih)':flags=lanczos",
                "-c:v", "libx264", "-crf", "30", "-preset", "slow",
                "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", preview])
    width, height, duration = probe_video(mp4)
    if not fresh(src, poster):
        ffmpeg(["-ss", f"{min(1.0, duration / 3):.2f}", "-i", mp4,
                "-frames:v", "1",
                "-vf", f"scale=-2:'min({PROMO_THUMB_HEIGHT},ih)':flags=lanczos",
                "-q:v", "4", poster])
    return {
        "type": "video", "name": display_name(src),
        "w": width, "h": height, "d": round(duration),
        "poster": web_path(poster), "preview": web_path(preview), "src": web_path(mp4),
    }, os.path.getsize(mp4) + os.path.getsize(preview) + os.path.getsize(poster)


def display_name(src):
    # macOS отдаёт кириллицу в разложенном виде (й = и + ˘), а в подписи
    # и в order.txt нужна обычная запись.
    return unicodedata.normalize("NFC", os.path.basename(src))


def promo_order(folder):
    """Имена файлов проекта в порядке order.txt, остальные — в конец по имени."""
    files = {
        unicodedata.normalize("NFC", n): n
        for n in os.listdir(folder)
        if os.path.splitext(n)[1].lower() in RASTER + VIDEO
    }
    order_file = os.path.join(folder, PROMO_ORDER)
    listed = []
    if os.path.exists(order_file):
        for line in open(order_file, encoding="utf-8"):
            name = unicodedata.normalize("NFC", line.strip())
            if not name or name.startswith("#"):
                continue
            if name in files and name not in listed:
                listed.append(name)
            elif name not in files:
                print(f"  order.txt: нет файла {os.path.basename(folder)}/{name}")
    rest = sorted((n for n in files if n not in listed), key=natural_key)
    if listed and rest:
        print(f"  order.txt: не перечислены в {os.path.basename(folder)}, "
              f"идут в конец: {', '.join(rest)}")
    return [files[n] for n in listed + rest]


def prune_promo(data):
    """Убрать из source/img/promo/ версии файлов, которых больше нет в promo-web/.

    После переименования или удаления промо старые webp/mp4 никто не открывает,
    но они уезжали бы в репозиторий и на хостинг.
    """
    keep = set()
    for items in data.values():
        for item in items:
            for key in ("low", "thumb", "full", "poster", "preview", "src"):
                if key in item:
                    keep.add(unicodedata.normalize("NFC", item[key]))
    removed = 0
    for folder, _, files in os.walk(PROMO_OUT, topdown=False):
        for name in files:
            path = os.path.join(folder, name)
            if unicodedata.normalize("NFC", web_path(path)) not in keep:
                os.remove(path)
                removed += 1
        if folder != PROMO_OUT and not os.listdir(folder):
            os.rmdir(folder)
    if removed:
        print(f"  промо: удалено устаревших версий: {removed}")


def promo_projects():
    """Папки проектов из всех источников промо: (имя, путь)."""
    found = {}
    for source in PROMO_SOURCES:
        if not os.path.isdir(source):
            continue
        for project in sorted(os.listdir(source), key=natural_key):
            folder = os.path.join(source, project)
            if not os.path.isdir(folder):
                continue
            if project in found:
                print(f"  ПРОМО ПРОПУСК {web_path(folder)}: проект с таким именем "
                      f"уже есть в {web_path(found[project])}")
                continue
            found[project] = folder
    return found.items()


def build_promo():
    # Без исходников нечего собирать — и нечего «чистить»: иначе prune_promo
    # снёс бы все собранные версии.
    if not any(os.path.isdir(source) for source in PROMO_SOURCES):
        return
    data = {}
    before = after = 0
    for project, folder in promo_projects():
        items = []
        for name in promo_order(folder):
            src = os.path.join(folder, name)
            ext = os.path.splitext(name)[1].lower()
            try:
                if ext in RASTER:
                    item, size = build_promo_image(project, src)
                elif ext in VIDEO:
                    item, size = build_promo_video(project, src)
                else:
                    continue
            except Exception as exc:
                print(f"  ПРОМО ПРОПУСК {os.path.relpath(src, ROOT)}: {exc}")
                continue
            items.append(item)
            before += os.path.getsize(src)
            after += size
        if items:
            data[project] = items
            print(f"  промо {project}: {len(items)} шт.")

    prune_promo(data)

    with open(PROMO_DATA, "w", encoding="utf-8") as fh:
        fh.write("// Собрано tools/build-images.py из promo-web/ и promo-project/ — руками не править.\n")
        fh.write("window.PROMO = ")
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.write(";\n")
    print(f"  промо: {human(before)} -> {human(after)}, список в {web_path(PROMO_DATA)}")


def stamp_assets():
    """Дописать к ссылкам на свои css/js метку версии: main.css?v=<хеш>.

    GitHub Pages разрешает браузеру 10 минут не перепроверять файлы. Сразу
    после пуша телефон брал новую страницу со старым main.css из кэша — и
    страница разваливалась. Метка меняется вместе с содержимым файла, поэтому
    изменённый файл браузер обязан скачать заново, а неизменённый берёт из кэша.
    """
    pages = [os.path.join(ROOT, "index.html")] + sorted(
        glob.glob(os.path.join(ROOT, "html", "*.html"))
    )
    pattern = re.compile(
        r'((?:href|src)\s*=\s*")([^"?#:]+\.(?:css|js))(?:\?v=[0-9a-f]*)?(")'
    )
    for page in pages:
        base = os.path.dirname(page)
        html = open(page, encoding="utf-8").read()

        def stamp(m):
            path = os.path.normpath(os.path.join(base, m.group(2)))
            if not os.path.isfile(path):
                return m.group(0)
            digest = hashlib.md5(open(path, "rb").read()).hexdigest()[:8]
            return f"{m.group(1)}{m.group(2)}?v={digest}{m.group(3)}"

        stamped = pattern.sub(stamp, html)
        if stamped != html:
            with open(page, "w", encoding="utf-8") as fh:
                fh.write(stamped)
            print(f"  версии css/js обновлены: {os.path.relpath(page, ROOT)}")


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
            delays = frame_delays(raw)
            mp4, did = convert_animation(src, raw.size, delays)
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

    print()
    build_promo()
    # После build_promo: он переписывает js/promo-data.js, метка должна
    # соответствовать уже новому файлу.
    stamp_assets()

    print(f"\nПересобрано файлов: {built}")
    print(f"Оригиналы:            {human(before)}")
    print(f"Версии для показа:    {human(after)}")
    print(f"Экономия трафика:     {(1 - after / before) * 100:.0f}%")


if __name__ == "__main__":
    main()
