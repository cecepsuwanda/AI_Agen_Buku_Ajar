"""Pembangun konteks prompt yang dipakai bersama oleh beberapa agent — MURNI.

Isi berkas ini adalah **penerjemahan state menjadi teks prompt**. Ia sengaja
dipisah dari agent-agent yang memakainya karena dua alasan:

* **Satu definisi, bukan dua.** Ringkasan bab sebelumnya dan daftar istilah
  muncul di prompt Chapter Planner maupun Chapter Writer. Menyalinnya ke dua
  berkas berarti keduanya akan menyimpang, dan penyimpangannya berupa prompt —
  yang tidak akan menimbulkan error, hanya bab yang kualitasnya berbeda tanpa
  ada yang tahu sebabnya.
* **Ia dapat diuji tanpa agent.** Ini aturan penyaringan yang nyata: ringkasan
  bab sendiri harus dikecualikan, urutan istilah harus deterministik. Aturan
  seperti itu layak punya tes langsung, bukan tes yang harus lebih dulu
  menyiapkan model palsu.

Seluruh fungsi di sini **murni**: masuk state, keluar tuple string. Tidak ada
model LLM, tidak ada berkas, dan tidak ada yang diubah.
"""

from __future__ import annotations

from typing import Sequence

from domain.book import BookSpec
from domain.chapter import Evidence
from domain.state import BookState


def book_title(book: BookState) -> str:
    """Judul buku — dari spesifikasi bila sudah ada, dari permintaan bila belum (MURNI).

    Keduanya memang dapat berbeda: ``BookSpec.title`` adalah judul yang
    **diputuskan** perencana, sedangkan ``BookRequest.title`` hanyalah usulan
    pengguna. Memakai yang pertama saat tersedia berarti prompt bab mengikuti
    keputusan itu, bukan menentangnya.
    """
    spec: BookSpec | None = book.spec
    return spec.title if spec else book.request.title


def book_language(book: BookState) -> str:
    """Bahasa keluaran buku (MURNI)."""
    spec: BookSpec | None = book.spec
    return spec.language if spec else book.request.language


def summaries_before(book: BookState, number: int) -> tuple[str, ...]:
    """Ringkasan bab-bab **sebelum** ``number``, terurut (MURNI).

    Bab ini sendiri dikecualikan: menyertakan ringkasan bab yang sedang
    direncanakan atau ditulis akan membuat model mengulang isinya sendiri. Bab
    sesudahnya juga dikecualikan — ringkasannya memang belum ada.
    """
    return tuple(f"Bab {n}: {book.summaries[n]}" for n in sorted(book.summaries) if n < number)


def terminology_lines(book: BookState) -> tuple[str, ...]:
    """Istilah yang sudah dipakai di bab-bab sebelumnya (MURNI).

    Diurutkan supaya prompt deterministik: state yang sama harus selalu
    menghasilkan prompt yang sama, kalau tidak, ``prompt_digest`` kehilangan
    gunanya sebagai alat pembanding kualitas antar-jalankan (§38).
    """
    return tuple(f"{term}: {book.terminology[term]}" for term in sorted(book.terminology))


def evidence_lines(evidence: Sequence[Evidence]) -> tuple[str, ...]:
    """Bukti retriever sebagai teks prompt, bernomor dan bersumber (MURNI).

    Nomor urutnya ada supaya peneliti dapat menyebut "bukti 3" alih-alih
    menyalin ulang kalimatnya — dan supaya manusia yang membaca temuan dapat
    kembali ke potongan yang dimaksud.

    Yang **selalu** ikut serta adalah ``source`` dan halaman. Menyusun daftar
    bukti tanpa keduanya akan menghasilkan bahan yang tampak sama tetapi tidak
    dapat diperiksa: model menyaringnya dengan baik, dan tidak ada satu pun cara
    untuk tahu dari halaman mana kalimat itu berasal.
    """
    lines: list[str] = []
    for index, item in enumerate(evidence, start=1):
        kepala = [item.source]
        if item.section.strip():
            kepala.append(item.section.strip())
        if item.page is not None:
            kepala.append(f"hlm. {item.page}")
        lines.append(f"[{index}] " + " — ".join(kepala) + "\n" + item.text.strip())
    return tuple(lines)


def evidence_ref(item: Evidence) -> str:
    """Penanda satu potongan bukti tanpa isinya (MURNI).

    Bagian kepala yang sama dengan :func:`evidence_lines`, tanpa nomor urut dan
    tanpa teksnya — karena yang dibutuhkan di sini bukan bacaan bagi model
    melainkan **identitas** bagi catatan §38: potongan mana yang ikut menentukan
    jawaban ini. Menyalin teksnya akan menggandakan isi buku ke dalam berkas log;
    menyebut nomor urutnya saja akan kehilangan arti begitu urutannya berubah di
    bab berikutnya.
    """
    parts = [item.source]
    if item.section.strip():
        parts.append(item.section.strip())
    if item.page is not None:
        parts.append(f"hlm. {item.page}")
    return " — ".join(parts)


__all__ = [
    "book_language",
    "book_title",
    "evidence_lines",
    "evidence_ref",
    "summaries_before",
    "terminology_lines",
]
