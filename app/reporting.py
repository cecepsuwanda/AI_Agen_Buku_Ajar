"""Implementasi :class:`domain.ports.Reporter`.

Dipisah dari ``app/logging_setup.py`` dengan sengaja: **pelaporan progres** dan
**log diagnosis** adalah dua hal berbeda dengan dua audiens berbeda. Yang
pertama untuk dosen yang menonton terminal; yang kedua untuk mesin yang membaca
``state/run.jsonl`` setelah kegagalan.

``RichReporter`` boleh mengimpor ``rich``; ``agents/`` tidak boleh menyentuh
``rich`` sama sekali — ia hanya memanggil method port ini. Itulah sebabnya
mengganti UI (mis. ke web nanti) tidak menuntut satu pun perubahan pada agent.

**Teks konsol sengaja ASCII-saja.** Konsol Windows warisan memakai cp1252, dan
karakter tipografis (``·``, ``—``, ``→``) melempar ``UnicodeEncodeError`` di
tengah pelaporan. Terjemahan UTF-8 di ``app/cli.py`` adalah perbaikan utamanya;
ASCII di sini adalah lapisan yang membuat kegagalan itu mustahil. Teks
Indonesia yang ditulis ke *berkas* tetap memakai UTF-8 penuh — berkas selalu
menyatakan encoding-nya secara eksplisit, konsol tidak.
"""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from domain.chapter import ChapterRecord, ReviewResult
from domain.enums import ChapterStatus, is_problem

#: Warna per status, agar tabel status terbaca sekilas.
_STATUS_STYLE: dict[ChapterStatus, str] = {
    ChapterStatus.APPROVED: "bold green",
    ChapterStatus.DRAFTED: "cyan",
    # Dua tahap penulisan sesudah draf (§19, §20) sederajat dengan DRAFTED:
    # keduanya berarti "karya tulis sudah ada dan bertambah", bukan "sedang
    # bermasalah" maupun "sudah selesai".
    ChapterStatus.EXAMPLES_WRITTEN: "cyan",
    ChapterStatus.EXERCISES_WRITTEN: "cyan",
    ChapterStatus.PLANNED: "dim",
    ChapterStatus.REVISION: "yellow",
    ChapterStatus.FAILED_REVIEW: "bold red",
    ChapterStatus.FAILED: "bold red",
}


class NullReporter:
    """Reporter yang tidak melakukan apa pun.

    Dipakai tes dan ``--quiet``: memastikan tidak ada jalur kode yang diam-diam
    bergantung pada keluaran terminal.
    """

    def info(self, message: str) -> None:
        """Abaikan."""

    def warn(self, message: str) -> None:
        """Abaikan."""

    def chapter_started(self, number: int, title: str) -> None:
        """Abaikan."""

    def stage(self, number: int, status: ChapterStatus) -> None:
        """Abaikan."""

    def gate_result(self, number: int, result: ReviewResult) -> None:
        """Abaikan."""

    def chapter_finished(self, record: ChapterRecord) -> None:
        """Abaikan."""


class RichReporter:
    """Reporter terminal berbasis ``rich``.

    Menahan satu :class:`~rich.console.Console` sehingga tabel dan teks tidak
    saling menimpa. Tidak menyimpan state bisnis apa pun.
    """

    def __init__(self, console: Console | None = None, *, verbose: bool = False) -> None:
        self._console = console or Console()
        self._verbose = verbose

    @property
    def console(self) -> Console:
        """Console yang dipakai (agar perintah dapat mencetak tabel dengannya)."""
        return self._console

    @property
    def verbose(self) -> bool:
        """True bila pelaporan rinci diminta.

        Dibaca batas kesalahan di ``app/commands.py`` untuk memutuskan apakah
        traceback perlu dicetak: pesan rapi untuk kesalahan yang terduga, dan
        traceback penuh hanya bila seseorang memang memintanya.
        """
        return self._verbose

    def info(self, message: str) -> None:
        """Cetak pesan informasi."""
        self._console.print(message)

    def warn(self, message: str) -> None:
        """Cetak peringatan — kuning, dan selalu tampil (bahkan tanpa ``--verbose``)."""
        self._console.print(f"[yellow]peringatan:[/yellow] {message}")

    def chapter_started(self, number: int, title: str) -> None:
        """Tandai mulainya sebuah bab."""
        self._console.rule(f"[bold]Bab {number}[/bold] - {title}", align="left")

    def stage(self, number: int, status: ChapterStatus) -> None:
        """Cetak satu tahap pipeline yang selesai.

        Hanya tampil dengan ``--verbose``: untuk 8 bab × 5 tahap, mencetak
        semuanya akan mengubur baris yang penting.
        """
        if self._verbose:
            style = _STATUS_STYLE.get(status, "white")
            self._console.print(f"  bab {number}: [{style}]{status.value}[/{style}]")

    def gate_result(self, number: int, result: ReviewResult) -> None:
        """Cetak vonis sebuah gate.

        Gate yang *dilewati* (``skipped``) hanya tampil dengan ``--verbose`` —
        itu memang bukan berita. Gate yang benar-benar memeriksa selalu tampil,
        beserta skornya.

        Vonis yang **menunggu keputusan manusia** (§44) dicetak sebelum yang
        lain, dan dengan kata "menunggu" alih-alih "ditolak": gate ini tidak
        menemukan kesalahan apa pun, dan mencetaknya sebagai penolakan akan
        membuat dosen mencari-cari apa yang salah pada bab yang sebenarnya
        hanya belum dibaca.
        """
        if result.skipped:
            if self._verbose:
                self._console.print(
                    f"  bab {number}: [dim]{result.gate} dilewati[/dim]"
                )
            return

        if result.blocked:
            self._console.print(
                f"  bab {number}: [yellow]{result.gate}: menunggu keputusan "
                f"manusia[/yellow]"
            )
            for line in result.feedback[:5]:
                self._console.print(f"      [dim]-[/dim] {line}")
            return

        if result.approved:
            self._console.print(
                f"  bab {number}: [green]lolos {result.gate}[/green] "
                f"(skor {result.score:.1f}/10)"
            )
            return

        self._console.print(
            f"  bab {number}: [red]ditolak {result.gate}[/red] "
            f"(skor {result.score:.1f}/10)"
        )
        for line in result.feedback[:5]:
            self._console.print(f"      [dim]-[/dim] {line}")

    def chapter_finished(self, record: ChapterRecord) -> None:
        """Cetak status akhir sebuah bab."""
        style = _STATUS_STYLE.get(record.status, "white")
        marker = "!" if is_problem(record.status) else "-"
        suffix = f" - {record.error}" if record.error else ""
        self._console.print(
            f"[{style}]{marker} bab {record.number}: {record.status.value}[/{style}]"
            f"[dim]{suffix}[/dim]"
        )

    # -- Helpers yang khusus UI (tidak ada di port) -------------------------
    def table(self, title: str, columns: list[str]) -> Table:
        """Buat tabel rich yang siap diisi pemanggil."""
        table = Table(title=title, header_style="bold")
        for column in columns:
            table.add_column(column)
        return table


__all__ = ["NullReporter", "RichReporter"]
