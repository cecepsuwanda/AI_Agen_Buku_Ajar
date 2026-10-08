"""Kompilasi LaTeX lewat ``latexmk`` (§26, §42).

Ini satu-satunya tempat di seluruh aplikasi yang menjalankan ``latexmk``, dan
satu-satunya yang memanggil ``subprocess``. Keduanya disengaja: menjalankan
program lain adalah pekerjaan batas sistem, dan batas sistem di proyek ini
tinggal di paket adapter (``ingestion/``, ``rag/``, ``latex/``) — bukan di
``domain/``, yang tidak boleh mengenal berkas, dan bukan di ``agents/``, yang
hanya boleh mengenal port.

**Kompilasi selalu berjalan di direktori kerja sementara.** Sumber yang
dibutuhkan disalin ke sana, perkakas dijalankan di sana, dan hanya PDF-nya yang
dipindahkan keluar. Alasannya bukan kerapian: ``latexmk`` menulis belasan berkas
bantu (``.aux``, ``.fls``, ``.fdb_latexmk``, ``.out``, …) yang isinya berubah
setiap kali dijalankan meski sumbernya sama, dan menjalankannya langsung di
``output/latex/`` — yang di-track git (§39) — berarti setiap kompilasi
menghasilkan diff yang tidak dapat dibaca siapa pun. Ketika berkas bantu itu
memang dibutuhkan untuk menelusuri masalah, ``keep_aux`` menyimpannya dan
:attr:`LatexmkCompiler.workdir` mengatakan di mana.

**Kegagalan kompilasi bukan pengecualian.** Perkakas yang tidak ada, batas waktu
yang lewat, dan galat LaTeX yang paling parah sekalipun semuanya dikembalikan
sebagai :class:`~domain.latex.LatexBuildResult`. Bab yang tidak dapat dikompilasi
adalah kondisi bisnis yang diharapkan (§35) — dan yang memutuskan apa artinya
adalah :func:`~domain.latex.build_problems`, bukan adapter ini.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Sequence

from domain.errors import ArtifactWriteError
from domain.latex import (
    LatexBuildResult,
    chapter_latex_filename,
    render_bibliography,
    render_main_tex,
)
from latex.artifacts import (
    BIBLIOGRAPHY_FILENAME,
    CHAPTERS_DIRNAME,
    atomic_write_text,
    templates_dir,
)
from latex.validator import parse_log

#: Nama pekerjaan berkas utama. Tetap, bukan acak: nama berkas muncul di dalam
#: pesan galat LaTeX, dan ``main.tex`` adalah nama yang dikenal siapa pun yang
#: pernah membuka proyek LaTeX.
MAIN_JOBNAME = "main"

#: Nama pekerjaan untuk kompilasi satu potongan bab. Berbeda dari ``main`` supaya
#: log keduanya tidak pernah tertukar saat ``keep_aux`` menyimpannya berdampingan.
PROBE_JOBNAME = "probe"

#: Berkas preamble yang disalin ke direktori kerja.
PREAMBLE_FILENAME = "preamble.tex"

#: Nama berkas utama di dalam template.
MAIN_TEMPLATE_FILENAME = "main.tex"

#: Batas waktu untuk **menguji** bahwa perkakasnya dapat dijalankan. Jauh lebih
#: pendek dari batas waktu kompilasi: yang dijalankan hanyalah pencetakan versi,
#: dan mesin yang butuh lebih dari ini untuk itu adalah mesin yang tidak akan
#: menyelesaikan kompilasi buku apa pun.
_PROBE_TIMEOUT_S = 30.0

#: Argumen pencetakan versi. Bukan ``--version``: ``latexmk`` adalah skrip Perl
#: dan hanya mengenal ``-v``.
_VERSION_FLAG = "-v"


class LatexmkCompiler:
    """Adapter :class:`~domain.ports.LatexCompiler` di atas ``latexmk``.

    Menerima **direktori LaTeX**, bukan jalur berkas satu per satu, dengan alasan
    yang sama seperti :class:`~latex.artifacts.FileLatexArtifacts`: layout §25
    hanya didefinisikan di satu tempat. Direktori itu dipakai untuk membaca
    potongan bab yang akan dirakit ``compile_book`` dan untuk menaruh PDF akhir.
    """

    def __init__(
        self,
        latex_dir: Path | None = None,
        *,
        engine: str = "latexmk",
        timeout_s: float = 180.0,
        keep_aux: bool = False,
        template_dir: Path | None = None,
    ) -> None:
        self._latex_dir = latex_dir
        self._engine = engine
        self._timeout_s = timeout_s
        self._keep_aux = keep_aux
        self._template_dir = template_dir
        self._workdir: Path | None = None

    # -- pemeriksaan ketersediaan ------------------------------------------
    def available(self) -> bool:
        """True bila perkakasnya bukan hanya ada di ``PATH``, tetapi dapat dijalankan.

        Dipakai composition root untuk memutuskan apakah gate §26 dipasang atau
        diganti pass-through. Bukan di dalam gate: gate yang memeriksa ``PATH``
        sendiri berarti keputusan tentang mesin ini diambil di dalam logika
        pemeriksaan bab, dan itu jenis keputusan yang tidak dapat diuji tanpa
        mesin ini.

        **Mengapa bukan ``shutil.which`` saja.** Berkas yang ada belum tentu
        perkakas yang berjalan. ``latexmk`` di MiKTeX adalah shim yang menuntut
        ``perl``; tanpa ``perl`` terpasang, shim-nya tetap ada di ``PATH``,
        ``which`` mengembalikan jalurnya, dan setiap pemanggilannya gagal dengan
        kode keluar 1. Mempercayai ``which`` berarti setiap bab ditolak oleh gate
        §26 dengan alasan yang salah — "potongan LaTeX-nya tidak dapat
        dikompilasi" — padahal yang tidak ada adalah perkakasnya. Karena itu
        yang dijalankan di sini adalah pencetakan versi, dan hasilnya yang
        dijawab.
        """
        if shutil.which(self._engine) is None:
            return False
        try:
            completed = subprocess.run(
                [self._engine, _VERSION_FLAG],
                capture_output=True,
                timeout=_PROBE_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return completed.returncode == 0

    @property
    def workdir(self) -> Path | None:
        """Direktori kerja kompilasi terakhir yang disimpan (``keep_aux``).

        ``None`` bila berkas bantu tidak diminta disimpan — bukan "belum ada
        kompilasi", karena direktori yang dibuang memang tidak lagi berguna.
        """
        return self._workdir

    @property
    def latex_dir(self) -> Path:
        """Direktori keluaran LaTeX; ``Path()`` bila tidak diberikan."""
        return self._latex_dir if self._latex_dir is not None else Path()

    # -- API publik ---------------------------------------------------------
    def compile_fragment(
        self,
        fragment: str,
        *,
        sources: Sequence[str] = (),
    ) -> LatexBuildResult:
        """Kompilasi satu potongan bab di dalam dokumen sekali pakai (§26).

        Bab yang ditulis gate §25 adalah potongan ``\\include``: ia tidak punya
        ``\\documentclass`` dan tidak dapat berdiri sendiri. Karena itu dokumen
        pembungkusnya dibuat di sini — preamble yang sama dengan buku, satu
        ``\\bibliography``, dan potongan babnya apa adanya.

        Hasilnya menjawab pertanyaan §26 yang sebenarnya: **apakah potongan ini
        dapat dikompilasi**. ``\\ref`` yang menunjuk bab lain akan tampak
        menggantung di sini meski tidak ada yang salah, dan karena itu
        :func:`~domain.latex.build_problems` tidak menghitungnya sebagai temuan
        yang menahan.
        """
        document = (
            "\\documentclass[11pt,a4paper,oneside]{book}\n"
            "\\input{preamble}\n"
            "\\begin{document}\n"
            f"{fragment.strip()}\n"
            "\\bibliography{references}\n"
            "\\end{document}\n"
        )
        return self._build(
            jobname=PROBE_JOBNAME,
            document=document,
            sources=sources,
            chapters=(),
        )

    def compile_book(
        self,
        *,
        title: str,
        chapter_numbers: Sequence[int],
        sources: Sequence[str] = (),
        destination: Path | None = None,
    ) -> LatexBuildResult:
        """Rakit dan kompilasi seluruh buku, lalu taruh PDF-nya (§42).

        ``main.tex`` tidak ditulis dari nol melainkan diisi dari template
        (``latex/templates/main.tex``, atau ``--latex-template``): prodi yang
        punya preamble sendiri tidak boleh kehilangan preamblenya hanya karena
        kita perlu menambahkan daftar bab.
        """
        template = self._read_template(MAIN_TEMPLATE_FILENAME)
        document = render_main_tex(template, title=title, chapter_numbers=chapter_numbers)
        return self._build(
            jobname=MAIN_JOBNAME,
            document=document,
            sources=sources,
            chapters=chapter_numbers,
            destination=destination,
        )

    # -- inti ---------------------------------------------------------------
    def _build(
        self,
        *,
        jobname: str,
        document: str,
        sources: Sequence[str],
        chapters: Sequence[int],
        destination: Path | None = None,
    ) -> LatexBuildResult:
        """Siapkan direktori kerja, jalankan perkakas, dan baca lognya."""
        workdir = Path(tempfile.mkdtemp(prefix="bukujar-latex-"))
        self._workdir = None
        try:
            atomic_write_text(workdir / f"{jobname}.tex", document)
            atomic_write_text(workdir / PREAMBLE_FILENAME, self._read_template(PREAMBLE_FILENAME))
            atomic_write_text(workdir / BIBLIOGRAPHY_FILENAME, render_bibliography(tuple(sources)))
            self._copy_chapters(workdir, chapters)

            result = self._run(workdir, jobname)
            if destination is not None and result.pdf_path:
                self._publish(Path(result.pdf_path), destination)
                result = result.model_copy(update={"pdf_path": str(destination)})
            return result
        finally:
            if self._keep_aux:
                self._workdir = workdir
            else:
                shutil.rmtree(workdir, ignore_errors=True)

    def _copy_chapters(self, workdir: Path, numbers: Sequence[int]) -> None:
        """Salin potongan bab dari ``output/latex/chapters/`` ke direktori kerja."""
        if not numbers:
            return
        source_dir = self.latex_dir / CHAPTERS_DIRNAME
        target_dir = workdir / CHAPTERS_DIRNAME
        target_dir.mkdir(parents=True, exist_ok=True)
        for number in numbers:
            name = chapter_latex_filename(number)
            origin = source_dir / name
            if not origin.is_file():
                # Berkas yang hilang bukan alasan memberhentikan seluruh
                # kompilasi: LaTeX akan melaporkannya sebagai ``File not found``
                # di log, dan laporan itu lebih baik daripada pengecualian di
                # tengah perakitan buku.
                continue
            shutil.copyfile(origin, target_dir / name)

    def _run(self, workdir: Path, jobname: str) -> LatexBuildResult:
        """Jalankan perkakas dan terjemahkan hasilnya menjadi :class:`LatexBuildResult`."""
        command = [
            self._engine,
            "-pdf",
            "-interaction=nonstopmode",
            f"{jobname}.tex",
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=workdir,
                capture_output=True,
                timeout=self._timeout_s,
                check=False,
            )
        except FileNotFoundError:
            return LatexBuildResult(
                ok=False,
                errors=(f"perkakas LaTeX {self._engine!r} tidak ditemukan di PATH",),
                log_excerpt=f"{self._engine} tidak dapat dijalankan.",
            )
        except subprocess.TimeoutExpired:
            return LatexBuildResult(
                ok=False,
                errors=(f"kompilasi melewati batas waktu {self._timeout_s:g} detik",),
                log_excerpt=f"{self._engine} dihentikan setelah {self._timeout_s:g} detik.",
            )

        # Log `.log` ditulis pdflatex dan memuat transkrip lengkapnya; keluaran
        # latexmk hanya menambahkan barisnya sendiri. Yang dibaca lebih dulu
        # adalah `.log`, dan keluaran proses hanya dipakai bila berkas itu tidak
        # ada — kompilasi yang gagal sebelum LaTeX sempat menulis apa pun.
        log = workdir / f"{jobname}.log"
        text = _decode(completed.stdout) + _decode(completed.stderr)
        if log.is_file():
            text = log.read_text(encoding="utf-8", errors="replace") + "\n" + text

        pdf = workdir / f"{jobname}.pdf"
        parsed = parse_log(text)
        produced = pdf.is_file()
        return parsed.model_copy(
            update={
                "ok": completed.returncode == 0 and produced and not parsed.errors,
                "pdf_path": str(pdf) if produced else "",
            }
        )

    def _publish(self, source: Path, destination: Path) -> None:
        """Pindahkan PDF hasil kompilasi ke ``destination`` (lewat berkas sementara).

        Bukan ``shutil.copyfile`` langsung: ``output/latex/book.pdf`` adalah
        hasil kerja yang dibuka orang di penampil PDF, dan penampil itu memegang
        handle-nya. Menulis ulang berkas yang sedang terbuka di Windows gagal
        dengan cara yang sama seperti penulisan state (§28) — karena itu polanya
        sama: tulis ke ``.tmp`` di direktori yang sama, lalu ``os.replace``.
        """
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp = destination.with_name(destination.name + ".tmp")
        try:
            shutil.copyfile(source, tmp)
            os.replace(tmp, destination)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise ArtifactWriteError(destination, str(exc)) from exc

    def _read_template(self, name: str) -> str:
        """Baca satu berkas template, dari ``--latex-template`` atau bawaan (§42)."""
        base = self._template_dir if self._template_dir is not None else templates_dir()
        return (base / name).read_text(encoding="utf-8")


def _decode(raw: bytes) -> str:
    """Terjemahkan keluaran proses menjadi teks, tanpa pernah melempar.

    Log LaTeX memuat UTF-8, tetapi perkakas di Windows kadang mencetak byte
    menurut code page konsol. Salah tebak encoding tidak boleh menggagalkan
    pembacaan masalah — yang dicari dari log ini adalah nama berkas dan nama
    perintah, dan keduanya tetap terbaca meski beberapa byte diganti.
    """
    return raw.decode("utf-8", errors="replace")


__all__ = [
    "MAIN_JOBNAME",
    "PREAMBLE_FILENAME",
    "PROBE_JOBNAME",
    "LatexmkCompiler",
]
