"""Penulisan berkas yang tahan gagal (§28) — satu-satunya tempat di ``memory/``
yang menyentuh ``os`` dan ``pathlib``.

Menulis JSON state secara langsung dengan ``open(path, "w")`` punya satu cacat
yang mahal: bila proses mati di tengah penulisan — atau bila pengguna menekan
Ctrl+C, atau laptopnya tidur — berkasnya tinggal setengah. State setengah jadi
lebih buruk daripada state yang hilang, karena ia **terbaca** dan gagal di
tempat yang tidak terduga.

Dua penguatan, keduanya berasal dari masalah nyata:

**Tulis ke ``.tmp`` di direktori yang sama, lalu ``os.replace()``.** ``os.replace``
atomik pada filesystem yang sama: pembaca melihat berkas lama atau berkas baru,
tidak pernah campurannya. Nama ``.tmp`` sengaja deterministik (bukan acak) supaya
berkas sisa mudah dikenali dan dibersihkan manusia.

**Ulangi ``os.replace`` saat ``PermissionError``.** Di Windows, antivirus dan
indexer dapat memegang handle berkas selama beberapa puluh milidetik setelah
penulis menutupnya. Kegagalan itu transien dan tidak ada hubungannya dengan
logika program — tetapi tanpa retry, ia muncul sebagai kegagalan acak yang
sulit dipercaya dan mustahil direproduksi.

**``newline="\\n"`` dan ``ensure_ascii=False``.** Keduanya bukan selera: teks
Indonesia berarti karakter non-ASCII, dan ``output/`` di-*track* git (§39).
Baris yang berubah menjadi CRLF di mesin Windows akan menghasilkan diff raksasa
pada setiap perubahan, dan ``\\u00e9`` yang ter-escape membuat berkas state tidak
dapat dibaca manusia — padahal dibaca manusia adalah satu-satunya alasan ia
berbentuk JSON dan bukan biner.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from domain.errors import StateCorruptError, StateWriteError

#: Berapa kali ``os.replace`` diulang sebelum menyerah.
_REPLACE_RETRIES = 8

#: Jeda antarpercobaan, dinaikkan linier. Total ~0,9 detik — cukup untuk
#: antivirus melepas handle, dan tidak cukup lama untuk membuat pengguna
#: mengira aplikasinya menggantung.
_REPLACE_BACKOFF_S = 0.025


def _replace_with_retry(tmp: Path, target: Path) -> None:
    """Pindahkan ``tmp`` ke ``target``, tahan terhadap handle Windows yang tertahan.

    :raises StateWriteError: bila seluruh percobaan gagal.
    """
    last: PermissionError | None = None
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(tmp, target)
            return
        except PermissionError as exc:  # pragma: no cover - bergantung timing OS
            last = exc
            time.sleep(_REPLACE_BACKOFF_S * (attempt + 1))

    raise StateWriteError(target) from last


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Tulis ``text`` ke ``path`` secara atomik.

    :raises StateWriteError: bila penulisan gagal.
    """
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding=encoding, newline="\n") as handle:
            handle.write(text)
        _replace_with_retry(tmp, path)
    except OSError as exc:
        raise StateWriteError(path) from exc


def atomic_write_json(path: Path, payload: Any, *, indent: int = 2) -> None:
    """Tulis ``payload`` sebagai JSON yang terbaca manusia, secara atomik.

    ``indent=2`` bukan kemewahan: berkas state ini dibaca orang saat mencari
    tahu mengapa sebuah bab berhenti di tengah jalan. JSON satu baris membuat
    pekerjaan itu berubah dari membaca menjadi mengurai.
    """
    text = json.dumps(payload, indent=indent, ensure_ascii=False)
    atomic_write_text(path, text + "\n")


def read_json(path: Path, *, encoding: str = "utf-8") -> Any:
    """Baca dan uraikan JSON dari ``path``.

    :raises StateCorruptError: bila berkasnya tidak ada, tidak dapat dibaca,
        atau isinya bukan JSON yang sah. Berkasnya **tidak pernah** dihapus
        otomatis — state yang rusak masih memuat pekerjaan yang mungkin tidak
        dapat dihasilkan ulang.
    """
    try:
        text = path.read_text(encoding=encoding)
    except FileNotFoundError as exc:
        raise StateCorruptError(path, "berkas tidak ditemukan") from exc
    except OSError as exc:  # pragma: no cover - bergantung izin OS
        raise StateCorruptError(path, f"tidak dapat dibaca: {exc}") from exc

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise StateCorruptError(path, f"JSON tidak sah pada baris {exc.lineno}: {exc.msg}") from exc


def file_exists(path: Path) -> bool:
    """True bila ``path`` ada dan merupakan berkas."""
    return path.is_file()


__all__ = ["atomic_write_json", "atomic_write_text", "file_exists", "read_json"]
