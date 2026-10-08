"""Compiler LaTeX palsu untuk ``--dry-run`` (§26, §42).

Sejajar dengan :class:`~models.dry_run.DryRunChatModel`, dan dengan pembenaran
yang sama: ``--dry-run`` menjanjikan jalur yang **tidak bergantung pada apa pun
di luar proses ini**. Menjalankan ``latexmk`` sungguhan akan membuat dry run
bergantung pada dua hal yang justru tidak sedang diperiksa — terpasangnya LaTeX
di mesin ini, dan kualitas isi bab yang disintesis dari skema saja.

Yang diperiksa ``--dry-run`` adalah **rangkaiannya**, dan itu tetap diperiksa di
sini: gate §26 benar-benar dibangun, benar-benar menerima port kompilasi, dan
benar-benar menjalankan alurnya sampai vonis. Yang tidak dilakukannya hanyalah
membaca log yang tidak ada.

Berbeda dari pass-through, gate dengan compiler ini **tidak** menandai dirinya
``skipped``: gate-nya berjalan, dan yang palsu hanyalah jawaban perkakasnya.
"""

from __future__ import annotations

from typing import Sequence

from domain.latex import LatexBuildResult


class DryRunLatexCompiler:
    """Adapter :class:`~domain.ports.LatexCompiler` yang selalu berhasil."""

    def __init__(self) -> None:
        self.calls = 0

    def compile_fragment(
        self,
        fragment: str,
        *,
        sources: Sequence[str] = (),
    ) -> LatexBuildResult:
        """Laporkan kompilasi bersih, dan catat berapa kali ditanya."""
        del fragment, sources
        self.calls += 1
        return LatexBuildResult(ok=True)


__all__ = ["DryRunLatexCompiler"]
