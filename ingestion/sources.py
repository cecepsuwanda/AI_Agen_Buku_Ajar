"""Batas filesystem untuk ingestion (§9, §10).

Paket ini punya dua pekerjaan yang sangat berbeda, dan batasnya sengaja ditaruh
di sini, di satu modul, bukan disebar:

* **Menemukan berkas** di ``input/references/`` dan **membacanya sebagai teks**.
  Keduanya pekerjaan batas sistem — menyentuh disk, mengenal nama berkas.
* **Menyerahkan teksnya kepada parser yang murni.** ``markdown_loader`` dan
  ``latex_loader`` tidak membuka berkas sama sekali; mereka menerima ``str`` dan
  mengembalikan potongan. Karena itu keduanya dapat diuji dengan teks buatan
  tanpa satu pun berkas contoh, dan itu memang alasan pemisahannya.

Berkas PDF tidak lewat sini: ia dibaca oleh pustakanya sendiri di
``pdf_loader``/``pdf_ocr`` — satu-satunya dua pintu tempat ``pypdf`` dan
``pymupdf`` boleh masuk.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from domain.errors import InputError

#: Akhiran berkas yang dianggap bahan rujukan.
#:
#: Daftar putih, bukan daftar hitam. Direktori ``input/references/`` hampir pasti
#: berisi hal-hal yang bukan bahan — ``.gitkeep``, berkas sementara hasil
#: pengunduhan, berkas catatan pribadi — dan daftar hitam akan memuat semuanya
#: dengan satu berkas teks yang tidak sengaja dan mengembangkannya menjadi kutipan
#: di dalam buku.
REFERENCE_SUFFIXES: frozenset[str] = frozenset({".pdf", ".md", ".txt", ".tex"})


def iter_reference_files(directory: str | Path) -> tuple[Path, ...]:
    """Seluruh berkas bahan di ``directory``, terurut dan deterministik (MURNI).

    ``sorted`` bukan kerapian: urutan pendaftaran berkas mempengaruhi urutan
    potongan di dalam indeks, dan indeks yang isinya bergantung pada urutan
    pembacaan direktori akan menghasilkan hasil pencarian yang berbeda di dua
    mesin untuk bahan yang sama. Urutan yang eksplisit membuat ``ingest`` dapat
    dibandingkan antar jalankan sampai ke potongannya.

    Pencariannya **rekursif**: bahan rujukan lazim disimpan per topik di dalam
    subdirektori, dan direktori yang lebih dalam tidak boleh diam-diam terlewat.
    Direktori yang tidak ada bukan kesalahan di sini — mengembalikan himpunan
    kosong membuat pemanggilnya yang memutuskan, dan pemanggilnya memang yang tahu
    apakah "belum ada bahan" itu keadaan yang wajar atau kegagalan.
    """
    root = Path(directory)
    if not root.is_dir():
        return ()
    return tuple(
        sorted(
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in REFERENCE_SUFFIXES
        )
    )


def read_text(path: str | Path, *, encoding: str = "utf-8") -> str:
    """Baca berkas teks, atau gagal dengan pesan yang menunjuk berkasnya.

    ``UnicodeDecodeError`` diterjemahkan menjadi :class:`~domain.errors.InputError`
    alih-alih dibiarkan sebagai traceback, dan ``errors="replace"`` sengaja
    **tidak** dipakai: penggantian karakter menghasilkan teks yang tampak wajar
    tetapi memuat tanda tanya di tengah kutipan — dan kutipan yang tidak sama
    dengan halaman aslinya tidak dapat diperiksa manusia, sementara penyebabnya
    (berkasnya bukan UTF-8) sudah tidak terlihat lagi.
    """
    target = Path(path)
    try:
        return target.read_text(encoding=encoding)
    except UnicodeDecodeError as exc:
        raise InputError("Bahan rujukan", target, f"bukan teks {encoding}") from exc
    except OSError as exc:
        raise InputError("Bahan rujukan", target, str(exc)) from exc


def iter_reference_texts(directory: str | Path) -> Iterator[tuple[Path, str]]:
    """Berkas teks bahan beserta isinya, satu per satu.

    Berkas PDF dilewati — ia bukan teks, dan pemanggilnya yang membacanya lewat
    :mod:`ingestion.pdf_loader`. Keduanya dipisah karena jalur PDF punya
    pertanyaan yang tidak dimiliki jalur teks ("apakah ini hasil scan?"), dan
    mencampurnya akan membuat fungsi ini menebak.
    """
    for path in iter_reference_files(directory):
        if path.suffix.lower() == ".pdf":
            continue
        yield path, read_text(path)


__all__ = [
    "REFERENCE_SUFFIXES",
    "iter_reference_files",
    "iter_reference_texts",
    "read_text",
]
