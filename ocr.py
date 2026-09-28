#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OCR-обёртка над tesseract для снимков окон.

Использование:
  ocr.py image.png                            — текст (--psm 6, rus+eng), stdout
  ocr.py image.png --psm 11 --format tsv      — слова с координатами
  ocr.py image.png -o text.txt                — записать результат в файл
  ocr.py image.png --format tsv --min-conf 60 — только уверенные слова

Форматы вывода:
  text — обычный текст из tesseract;
  tsv  — построчно: слово TAB уверенность TAB left TAB top TAB width TAB height
         (координаты слова нужны для будущего клика; сам клик не выполняется).

Код завершения:
  0 — распознавание выполнено;
  1 — ошибка tesseract (нет файла, нет языков и т.п.).
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kwinlib  # noqa: E402


def main():
    ap = argparse.ArgumentParser(
        prog="ocr.py", description="OCR изображения через tesseract (rus+eng)."
    )
    ap.add_argument("image", help="путь к изображению (PNG)")
    ap.add_argument(
        "--psm",
        type=int,
        default=None,
        help="режим сегментации tesseract (6 — текст, 11 — слова/sparse)",
    )
    ap.add_argument(
        "--lang", default="rus+eng", help="языки tesseract (по умолчанию rus+eng)"
    )
    ap.add_argument(
        "--format",
        choices=["text", "tsv"],
        default="text",
        help="формат вывода (по умолчанию text)",
    )
    ap.add_argument(
        "--min-conf",
        type=float,
        default=0,
        help="для tsv: отсекать слова с уверенностью ниже порога",
    )
    ap.add_argument(
        "-o", "--output", default=None, help="файл для результата (по умолчанию stdout)"
    )
    args = ap.parse_args()

    if not os.path.exists(args.image):
        print("файл не найден: %s" % args.image, file=sys.stderr)
        sys.exit(1)

    if args.format == "tsv":
        psm = args.psm if args.psm is not None else 11
        words = kwinlib.ocr_words(
            args.image, lang=args.lang, psm=psm, min_conf=args.min_conf
        )
        out_lines = ["\t".join(str(v) for v in w) for w in words]
    else:
        psm = args.psm if args.psm is not None else 6
        text = kwinlib.ocr_text(args.image, lang=args.lang, psm=psm)
        if not text:
            print("tesseract не вернул текст (проверьте файл и языки)", file=sys.stderr)
            sys.exit(1)
        out_lines = [text.rstrip("\n")]

    if args.output:
        parent = os.path.dirname(os.path.abspath(args.output))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            if args.format == "tsv":
                f.write("\n".join(out_lines) + "\n")
            else:
                f.write(out_lines[0] + "\n")
        print("результат записан: %s" % os.path.abspath(args.output), file=sys.stderr)
    else:
        if args.format == "tsv":
            for line in out_lines:
                print(line)
        else:
            print(out_lines[0])
    sys.exit(0)


if __name__ == "__main__":
    main()
