"""Rendering Markdown dari record bab — MURNI.

Markdown adalah **turunan** dari ``ChapterRecord``, bukan sumber kebenaran.
Karena render ini murni dan murah, bab yang sudah ``APPROVED`` tetapi kehilangan
berkas ``.md``-nya cukup dirender ulang — tidak perlu ditulis ulang oleh LLM.
"""

from __future__ import annotations

from domain.book import ChapterSpec
from domain.chapter import ChapterDraft


def render_chapter_markdown(
    draft: ChapterDraft,
    *,
    number: int,
    spec: ChapterSpec | None = None,
) -> str:
    """Render satu draf bab menjadi Markdown lengkap."""
    lines: list[str] = [f"# Bab {number}. {draft.title}", ""]

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
