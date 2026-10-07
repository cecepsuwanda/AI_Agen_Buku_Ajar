"""Rendering Markdown dari record bab — MURNI.

Markdown adalah **turunan** dari ``ChapterRecord``, bukan sumber kebenaran.
Karena render ini murni dan murah, bab yang sudah ``APPROVED`` tetapi kehilangan
berkas ``.md``-nya cukup dirender ulang — tidak perlu ditulis ulang oleh LLM.
"""

from __future__ import annotations

from domain.book import ChapterSpec
from domain.chapter import ChapterDraft

#: Pemisah yang lazim dipakai model setelah nomor bab ("Bab 3:", "Bab 3 -", ...).
_PREFIX_SEPARATORS = ":.-–—"


def strip_chapter_prefix(title: str) -> str:
    """Buang awalan penomoran bab yang ditambahkan model (MURNI).

    Penulis cenderung mengisi ``title`` dengan ``"Bab 3: Analisis Leksikal"``
    meski kontraknya hanya meminta judulnya: nomor bab ada di header prompt, dan
    menyalinnya kembali terasa wajar. Karena :func:`render_chapter_markdown`
    menambahkan nomornya sendiri, hasilnya menjadi ``"# Bab 3. Bab 3: ..."``.

    Nomor yang tertulis di judul sengaja **tidak** dicocokkan dengan nomor bab
    yang sebenarnya. Judul ``"Bab 2: ..."`` pada bab 3 tetap dibersihkan: nomor
    yang benar adalah milik perender, dan judul yang salah nomor lebih baik
    kehilangan nomornya daripada mempertahankan nomor yang keliru.

    Judul yang setelah dibersihkan menjadi kosong (mis. ``"Bab 3"``) dikembalikan
    apa adanya — kehilangan judul lebih buruk daripada judul yang berlebih.

    Dipakai juga sebagai jaring pengaman: ``description`` pada
    :attr:`~domain.chapter.ChapterDraft.title` mencegahnya di hulu, tetapi model
    tetap dapat mengabaikannya, dan state lama sudah memuat judul yang berawalan.
    """
    text = title.strip()
    if text[:3].lower() != "bab":
        return text

    rest = text[3:].lstrip()
    digits = ""
    while rest and rest[0].isdigit():
        digits, rest = digits + rest[0], rest[1:]
    if not digits:
        return text

    rest = rest.lstrip()
    if rest and rest[0] in _PREFIX_SEPARATORS:
        rest = rest[1:].lstrip()

    return rest or text


def render_chapter_markdown(
    draft: ChapterDraft,
    *,
    number: int,
    spec: ChapterSpec | None = None,
) -> str:
    """Render satu draf bab menjadi Markdown lengkap."""
    heading = strip_chapter_prefix(draft.title)
    lines: list[str] = [f"# Bab {number}. {heading}", ""]

    if draft.learning_objectives:
        lines.append("## Tujuan Pembelajaran")
        lines.append("")
        for objective in draft.learning_objectives:
            lines.append(f"- {objective}")
        lines.append("")

    for section in draft.sections:
        level = min(max(section.level, 2), 5)
        lines.append(f"{'#' * level} {section.heading}")
        lines.append("")
        if section.body.strip():
            lines.append(section.body.strip())
            lines.append("")

    if draft.examples:
        lines.append("## Contoh")
        lines.append("")
        for index, example in enumerate(draft.examples, start=1):
            lines.append(f"**Contoh {number}.{index}**")
            lines.append("")
            lines.append(example.strip())
            lines.append("")

    if draft.exercises:
        lines.append("## Latihan")
        lines.append("")
        for index, exercise in enumerate(draft.exercises, start=1):
            lines.append(f"{index}. {exercise.strip()}")
        lines.append("")

    if draft.citations:
        lines.append("## Rujukan")
        lines.append("")
        for citation in draft.citations:
            lines.append(f"- {citation}")
        lines.append("")

    if draft.unresolved_claims:
        # §34: klaim tanpa bukti ditandai terang-terangan, bukan disembunyikan.
        # Blok ini sengaja ditulis sebagai peringatan agar tidak lolos ke buku
        # final tanpa disadari.
        lines.append("> **Catatan verifikasi** — klaim berikut belum memiliki bukti")
        lines.append("> pendukung dari knowledge base dan perlu diverifikasi dosen:")
        lines.append(">")
        for claim in draft.unresolved_claims:
            lines.append(f"> - {claim.strip()}")
        lines.append("")

    if spec is not None and spec.references:
        lines.append("## Referensi Bab")
        lines.append("")
        for reference in spec.references:
            lines.append(f"- {reference}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_book_markdown(chapters: list[tuple[int, str]], *, title: str) -> str:
    """Gabungkan Markdown bab-bab menjadi satu berkas buku.

    ``chapters`` adalah daftar ``(nomor, markdown)``; pemanggil yang menentukan
    urutannya agar fungsi ini tetap bebas dari urusan IO dan state.
    """
    parts = [f"# {title}", ""]
    for _, markdown in sorted(chapters, key=lambda item: item[0]):
        parts.append(markdown.rstrip())
        parts.append("")
        parts.append("---")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def chapter_filename(number: int) -> str:
    """Nama berkas Markdown untuk bab ``number`` (mis. ``chapter01.md``)."""
    return f"chapter{number:02d}.md"
