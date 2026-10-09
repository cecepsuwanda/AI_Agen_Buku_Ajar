"""Menjalankan rencana kompilasi (§25, §26) — tanpa memasang LaTeX.

``latex/compiler.py`` adalah satu-satunya berkas yang menjalankan program lain,
dan sampai berkas ini ada, satu-satunya cara mengujinya adalah memasang LaTeX
lebih dulu. Itu berarti bagian adapter yang paling mudah salah — **keluaran
langkah mana yang dibaca** — hanya terbukti di mesin yang kebetulan punya
perkakasnya. Tes live membuktikan tebakan kita tentang bentuk log MiKTeX; tes ini
membuktikan bahwa keluaran yang salah tidak pernah sampai ke sana.

Yang dipalsukan hanyalah ``subprocess``: perintahnya tidak dijalankan, tetapi
berkas yang dihasilkan di direktori kerja tetap ditulis, karena berkas itulah
yang dibaca adapter setelahnya. Batasnya sengaja ditaruh di situ — di atasnya
semua kode nyata, di bawahnya tidak ada apa-apa.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping

import pytest

import latex.compiler as compiler_module
from domain.latex import LatexBuildResult
from latex.compiler import PROBE_JOBNAME, LatexToolchainCompiler

#: Peringatan yang **wajar** pada lintasan pertama: setiap ``\\cite`` ke depan
#: belum punya entri sampai BibTeX menulis ``.bbl``. Bila ia ikut terbaca,
#: setiap bab yang sehat ditolak karena rujukan yang sebenarnya baik-baik saja.
STALE = "LaTeX Warning: Citation `karangan-1a2b3c4d' on page 1 undefined on input line 7.\n"


class FakeProcess:
    """Yang dibaca ``subprocess.run``: kode keluar dan dua aliran keluaran."""

    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout.encode("utf-8")
        self.stderr = stderr.encode("utf-8")


class FakeToolchain:
    """Pengganti modul ``subprocess`` yang menulis berkas seperti perkakas asli.

    Setiap pemanggilan dicatat di :attr:`calls`, sehingga tes dapat memeriksa
    **program apa yang benar-benar dijalankan** — pertanyaan yang tidak dapat
    dijawab tes live ketika seluruh rencananya berjalan lancar.
    """

    TimeoutExpired = subprocess.TimeoutExpired

    def __init__(
        self,
        *,
        jobname: str = PROBE_JOBNAME,
        engine: str = "pdflatex",
        engine_logs: Iterable[str] = (),
        engine_stdout: Iterable[str] = (),
        fail_on: str | None = None,
        missing: str | None = None,
        omit_pdf: bool = False,
    ) -> None:
        self.calls: list[list[str]] = []
        self._jobname = jobname
        self._engine = engine
        self._engine_logs = list(engine_logs)
        self._engine_stdout = list(engine_stdout)
        self._fail_on = fail_on
        self._missing = missing
        self._omit_pdf = omit_pdf
        self._engine_calls = 0

    def run(self, argv: Any, *, cwd: Path, **kwargs: Any) -> FakeProcess:
        command = [str(part) for part in argv]
        self.calls.append(command)
        program = command[0]

        if program == self._missing:
            raise FileNotFoundError(program)

        if program == self._engine:
            index = self._engine_calls
            self._engine_calls += 1
            # Lintasan pertama menulis log yang masih memuat peringatan basi;
            # lintasan berikutnya menimpanya. Itulah yang terjadi sungguhan.
            (cwd / f"{self._jobname}.log").write_text(
                _nth(self._engine_logs, index, ""), encoding="utf-8"
            )
            if program == self._fail_on:
                return FakeProcess(1, stdout=f"keluaran {program} yang gagal\n")
            if not self._omit_pdf:
                (cwd / f"{self._jobname}.pdf").write_bytes(b"%PDF-1.4")
            return FakeProcess(0, stdout=_nth(self._engine_stdout, index, ""))

        if program == self._fail_on:
            return FakeProcess(1, stdout=f"keluaran {program} yang gagal\n")
        return FakeProcess(0, stdout=f"keluaran {program}\n")


class FakeProbe:
    """``subprocess`` palsu untuk :meth:`available` — hanya mencatat dan menjawab."""

    TimeoutExpired = subprocess.TimeoutExpired

    def __init__(self, failing: Iterable[str] = ()) -> None:
        self.calls: list[list[str]] = []
        self._failing = set(failing)

    def run(self, argv: Any, **kwargs: Any) -> FakeProcess:
        command = [str(part) for part in argv]
        self.calls.append(command)
        return FakeProcess(1 if command[0] in self._failing else 0)


class FakeWhich:
    """``shutil.which`` palsu: yang menentukan hanyalah apa yang ada di ``PATH``."""

    def __init__(self, present: Mapping[str, str]) -> None:
        self._present = dict(present)

    def which(self, name: str) -> str | None:
        return self._present.get(name)


def _nth(values: list[str], index: int, default: str) -> str:
    """Nilai ke-``index``, atau yang terakhir bila daftarnya lebih pendek.

    Daftar yang lebih pendek dari jumlah lintasan berarti "dan seterusnya sama";
    itu yang membuat tes dapat menyebut hanya lintasan yang menarik.
    """
    if not values:
        return default
    return values[min(index, len(values) - 1)]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Pasang satu :class:`FakeToolchain` sebagai ``subprocess`` modul compiler."""
    made = FakeToolchain()
    monkeypatch.setattr(compiler_module, "subprocess", made)
    return made


def _compiler(**overrides: Any) -> LatexToolchainCompiler:
    """Compiler tanpa direktori LaTeX: yang diuji di sini bukan perakitan bab."""
    return LatexToolchainCompiler(**overrides)


# ---------------------------------------------------------------------------
# Keluaran langkah mana yang dibaca
# ---------------------------------------------------------------------------
def test_only_the_last_pass_is_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Peringatan lintasan pertama tidak boleh ikut terbaca (ini regresi).

    Rencana ``pdflatex`` menjalankan LaTeX sebelum BibTeX, dan pada lintasan itu
    setiap sitasi memang belum punya entri. Menumpuk keluaran seluruh lintasan
    membuat hasil akhirnya bergantung pada urutan langkah, bukan pada keadaan
    dokumen yang sebenarnya.
    """
    monkeypatch.setattr(
        compiler_module,
        "subprocess",
        FakeToolchain(engine_logs=[STALE, ""], engine_stdout=[STALE, ""]),
    )

    result = _compiler().compile_fragment("\\chapter{Satu}\n", sources=("Sumber",))

    assert result.undefined_citations == ()
    assert result.ok is True


def test_all_four_steps_of_a_citing_document_are_run(fake: Any) -> None:
    """Potongan bab selalu memuat ``\\bibliography``, jadi BibTeX selalu ikut."""
    _compiler().compile_fragment("\\chapter{Satu}\n", sources=("Sumber",))

    assert [call[0] for call in fake.calls] == ["pdflatex", "bibtex", "pdflatex", "pdflatex"]


def test_the_engine_never_receives_the_pdf_flag(fake: Any) -> None:
    """``-pdf`` milik ``latexmk``; ``pdflatex -pdf`` menggagalkan kompilasi di MiKTeX."""
    _compiler().compile_fragment("\\chapter{Satu}\n", sources=("Sumber",))

    for call in fake.calls:
        assert "-pdf" not in call, f"{call[0]} mendapat opsi milik mesin lain"


def test_a_plan_that_finishes_without_a_pdf_is_not_a_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Kode keluar 0 tanpa PDF bukan kompilasi yang berhasil."""
    monkeypatch.setattr(compiler_module, "subprocess", FakeToolchain(omit_pdf=True))

    result = _compiler().compile_fragment("\\chapter{Satu}\n", sources=("Sumber",))

    assert result.ok is False
    assert result.pdf_path == ""


# ---------------------------------------------------------------------------
# Berhenti pada langkah yang gagal
# ---------------------------------------------------------------------------
def test_the_plan_stops_at_the_first_failed_step(monkeypatch: pytest.MonkeyPatch) -> None:
    """Melanjutkan setelah BibTeX gagal berarti mengejar galat sitasi yang menutupi
    penyebab sebenarnya."""
    fake = FakeToolchain(fail_on="bibtex")
    monkeypatch.setattr(compiler_module, "subprocess", fake)

    result = _compiler().compile_fragment("\\chapter{Satu}\n", sources=("Sumber",))

    assert [call[0] for call in fake.calls] == ["pdflatex", "bibtex"]
    assert result.ok is False


def test_a_bibtex_failure_is_reported_as_bibtex(monkeypatch: pytest.MonkeyPatch) -> None:
    """BibTeX tidak menulis baris ``!`` di ``.log``; tanpa ini kegagalannya sunyi.

    Dan yang dilaporkan harus BibTeX, bukan sitasinya: ``.log`` yang masih ada
    di direktori kerja adalah milik lintasan pertama, dan ia memuat setiap
    ``Citation ... undefined`` yang belum sempat diselesaikan.
    """
    monkeypatch.setattr(
        compiler_module, "subprocess", FakeToolchain(engine_logs=[STALE], fail_on="bibtex")
    )

    result = _compiler().compile_fragment("\\chapter{Satu}\n", sources=("Sumber",))

    assert any("bibtex gagal" in error for error in result.errors), result.errors
    assert result.undefined_citations == ()


def test_the_error_names_the_step_that_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Perbaikan §26 butuh tahu langkah mana yang jatuh, bukan hanya bahwa ada yang jatuh."""
    fake = FakeToolchain(fail_on="pdflatex")
    monkeypatch.setattr(compiler_module, "subprocess", fake)

    result = _compiler().compile_fragment("\\chapter{Satu}\n", sources=("Sumber",))

    assert len(fake.calls) == 1, "kompilasi yang gagal pada lintasan pertama tidak mengulang"
    assert any("menulis .aux" in error for error in result.errors), result.errors


def test_a_missing_program_is_an_answer_not_an_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Perkakas yang tidak ada adalah kondisi bisnis yang diharapkan (§35)."""
    monkeypatch.setattr(compiler_module, "subprocess", FakeToolchain(missing="pdflatex"))

    result = _compiler().compile_fragment("\\chapter{Satu}\n", sources=("Sumber",))

    assert result == LatexBuildResult(
        ok=False,
        errors=("perkakas LaTeX 'pdflatex' tidak ditemukan di PATH",),
        log_excerpt="pdflatex tidak dapat dijalankan (lintasan LaTeX; menulis .aux).",
    )


# ---------------------------------------------------------------------------
# Pemeriksaan ketersediaan
# ---------------------------------------------------------------------------
def _patch_availability(
    monkeypatch: pytest.MonkeyPatch,
    *,
    which: Mapping[str, str] | None = None,
    failing: Iterable[str] = (),
) -> FakeProbe:
    """Pasang ``shutil.which`` dan ``subprocess`` yang hasilnya dapat diatur."""
    probe = FakeProbe(failing)
    monkeypatch.setattr(compiler_module, "subprocess", probe)
    monkeypatch.setattr(compiler_module, "shutil", FakeWhich(which or {}))
    return probe


def test_availability_probes_every_program_the_plan_needs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mesin tanpa BibTeX harus dilaporkan tidak siap, bukan gagal di tiap bab."""
    probe = _patch_availability(
        monkeypatch,
        which={"pdflatex": "C:/tex/pdflatex.exe", "bibtex": "C:/tex/bibtex.exe"},
    )

    assert _compiler().available() is True
    assert sorted({call[0] for call in probe.calls}) == ["bibtex", "pdflatex"]


def test_availability_is_false_when_one_program_merely_refuses_to_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Berkas yang ada belum tentu perkakas yang berjalan — ``latexmk`` di MiKTeX."""
    _patch_availability(
        monkeypatch,
        which={"pdflatex": "C:/tex/pdflatex.exe", "bibtex": "C:/tex/bibtex.exe"},
        failing=("bibtex",),
    )

    assert _compiler().available() is False


def test_a_self_driving_engine_needs_only_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    """``latexmk`` menjalankan BibTeX sendiri; ia tidak boleh dituntut menemukan ``bibtex``."""
    probe = _patch_availability(monkeypatch, which={"latexmk": "/usr/bin/latexmk"})

    assert _compiler(engine="latexmk").available() is True
    assert [call[0] for call in probe.calls] == ["latexmk"]


def test_a_program_missing_from_the_path_is_not_probed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Percobaan menjalankan program yang tidak ada hanya menambah pengecualian."""
    probe = _patch_availability(monkeypatch, which={})

    assert _compiler().available() is False
    assert probe.calls == []
