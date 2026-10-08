"""Parser log LaTeX menjadi :class:`~domain.latex.LatexBuildResult` (§26).

Fungsi di berkas ini **murni terhadap teks**: masuk satu string log, keluar satu
objek; tidak ada berkas yang dibuka dan tidak ada ``latexmk`` yang dijalankan.
Itulah yang membuat kedelapan jenis masalah §26 dapat diuji dengan berkas log
contoh di ``tests/data/latex_logs/`` — termasuk masalah yang perkakas LaTeX-nya
belum tentu terpasang di mesin yang menjalankan tes. Kompilasi sungguhannya
diuji terpisah dan ditandai ``live``.

**Pemetaan kedelapan jenis §26 ke field hasil.** Dua di antaranya bergabung, dan
itu disengaja:

============================ ==========================================
jenis §26                    field
============================ ==========================================
galat kompilasi              ``errors``
rujukan menggantung          ``undefined_refs``
sitasi menggantung           ``undefined_citations``
gambar tidak ditemukan       ``missing_figures``
label hilang                 ``undefined_refs`` — ``\\ref`` tanpa
                             ``\\label`` *adalah* label yang hilang,
                             dibaca dari sisi yang memakainya
persamaan rusak              ``errors`` — LaTeX melaporkannya sebagai
                             galat (``! Missing $ inserted.``), bukan
                             sebagai peringatan
kotak melebihi halaman       ``overfull_boxes``
label ganda                  ``duplicate_labels``
============================ ==========================================

**Mengapa ``re`` boleh di sini dan tidak boleh di ``domain/``.** Yang diparsing
adalah keluaran program lain — formatnya bebas berubah antar versi, ia memakai
tanda kutip yang berbeda-beda, dan ia penuh variasi yang tidak beraturan. Itu
pekerjaan ekspresi reguler, bukan pekerjaan pemindaian braket yang ditulis
tangan. ``domain/latex.py`` sengaja tidak menyentuhnya: yang tinggal di sana
adalah keputusan yang tidak bergantung format, seperti apa arti sebuah temuan.
"""

from __future__ import annotations

import re

from domain.latex import LatexBuildResult, log_excerpt

#: Tanda buka/tutup kutipan yang dipakai LaTeX untuk nama di dalam pesannya.
#:
#: LaTeX2e klasik memakai `` ` `` dan ``'``; distribusi yang lebih baru kadang
#: memakai tanda kutip melengkung. Keduanya diterima karena log dari mesin lain
#: tidak dapat dipilih, dan parser yang hanya menerima satu gaya akan melaporkan
#: "tidak ada masalah" pada log yang justru penuh masalah.
_QUOTE_OPEN = "[`‘]"
_QUOTE_CLOSE = "['’]"

#: Baris galat LaTeX. Selalu dimulai ``!`` — termasuk ``! LaTeX Error:`` dan
#: ``! Emergency stop.``
_ERROR_RE = re.compile(r"^!\s*(.*)$")

_UNDEFINED_REF_RE = re.compile(rf"LaTeX Warning: Reference {_QUOTE_OPEN}(.*?){_QUOTE_CLOSE}")
_UNDEFINED_CITE_RE = re.compile(rf"LaTeX Warning: Citation {_QUOTE_OPEN}(.*?){_QUOTE_CLOSE}")
_DUPLICATE_LABEL_RE = re.compile(
    rf"LaTeX Warning: Label {_QUOTE_OPEN}(.*?){_QUOTE_CLOSE} multiply defined"
)
_MISSING_FIGURE_RE = re.compile(
    rf"File {_QUOTE_OPEN}(.*?){_QUOTE_CLOSE} not found"
)
_OVERFULL_RE = re.compile(r"^Overfull \\[hv]box .*$")

#: Baris peringatan yang tidak termasuk satu pun jenis di atas. Dipotong, karena
#: log satu buku dapat memuat ratusan baris seperti ini dan tidak satu pun
#: di antaranya dapat dikerjakan penulis bab.
_GENERIC_WARNING = "Warning:"
_MAX_WARNINGS = 10


def parse_log(text: str) -> LatexBuildResult:
    """Baca log LaTeX dan kelompokkan masalahnya menurut jenis (§26) (MURNI).

    Satu lintasan atas baris log, dan setiap baris hanya boleh masuk ke satu
    kelompok. Urutan pemeriksaannya penting: ``LaTeX Warning: Reference `x' on
    page 1 undefined`` juga memuat kata ``Warning:``, sehingga baris yang
    tergolong jenis tertentu harus dikenali **sebelum** kelompok peringatan
    umum — kalau tidak, setiap rujukan menggantung akan dilaporkan dua kali,
    sekali sebagai temuan yang dapat dikerjakan dan sekali sebagai kebisingan.

    ``ok`` di sini berarti "tidak ada galat yang terbaca dari log" saja. Adapter
    :mod:`latex.compiler` yang melengkapinya dengan kode keluar dan keberadaan
    PDF; pemisahan itu membuat berkas log contoh dapat diuji tanpa LaTeX
    terpasang sama sekali.
    """
    errors: dict[str, None] = {}
    warnings: dict[str, None] = {}
    undefined_refs: dict[str, None] = {}
    undefined_citations: dict[str, None] = {}
    duplicate_labels: dict[str, None] = {}
    missing_figures: dict[str, None] = {}
    overfull: dict[str, None] = {}

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue

        # Gambar yang hilang diperiksa **sebelum** galat umum, karena LaTeX
        # melaporkannya dengan dua bentuk yang berbeda — ``! LaTeX Error: File
        # `x.png' not found.`` yang fatal, dan ``LaTeX Warning: File ...`` yang
        # tidak — dan bentuk yang fatal itu dimulai dengan ``!``. Diperiksa
        # belakangan, ia akan selalu tertelan oleh kelompok galat dan field
        # ``missing_figures`` tidak akan pernah terisi meski §26 menyebutnya
        # tersendiri.
        figure = _MISSING_FIGURE_RE.search(stripped)
        if figure is not None:
            name = figure.group(1).strip()
            if name:
                missing_figures.setdefault(name, None)
            continue

        error = _ERROR_RE.match(stripped)
        if error is not None:
            message = error.group(1).strip()
            if message:
                errors.setdefault(message, None)
            continue

        if _OVERFULL_RE.match(stripped):
            overfull.setdefault(stripped, None)
            continue

        classifiers: tuple[tuple[re.Pattern[str], dict[str, None]], ...] = (
            (_UNDEFINED_REF_RE, undefined_refs),
            (_UNDEFINED_CITE_RE, undefined_citations),
            (_DUPLICATE_LABEL_RE, duplicate_labels),
        )
        classified = False
        for pattern, bucket in classifiers:
            match = pattern.search(stripped)
            if match is not None:
                name = match.group(1).strip()
                if name:
                    bucket.setdefault(name, None)
                classified = True
                break
        if not classified and _GENERIC_WARNING in stripped:
            warnings.setdefault(stripped, None)

    found_errors = tuple(errors)
    return LatexBuildResult(
        ok=not found_errors,
        errors=found_errors,
        warnings=tuple(list(warnings)[:_MAX_WARNINGS]),
        undefined_refs=tuple(undefined_refs),
        undefined_citations=tuple(undefined_citations),
        duplicate_labels=tuple(duplicate_labels),
        missing_figures=tuple(missing_figures),
        overfull_boxes=tuple(overfull),
        log_excerpt=log_excerpt(text),
    )


__all__ = ["parse_log"]
