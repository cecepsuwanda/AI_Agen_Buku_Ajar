"""Antarmuka baris perintah (typer).

Disiplin tunggal berkas ini: **hanya mem-parse argumen**. Ia tidak membaca
berkas, tidak merakit objek, dan tidak tahu apa itu Ollama. Semuanya
didelegasikan ke ``app/commands.py`` di atas objek parameter yang beku.

Alasannya bukan estetika: karena seluruh pekerjaan berada di fungsi ``do_*``
yang menerima parameter biasa, setiap perintah dapat diuji dengan memanggil
fungsinya langsung — tanpa ``CliRunner``, tanpa menangkap stdout, tanpa
menyalak argumen. CLI-nya sendiri diuji sekali di ``test_cli_offline.py``
semata untuk memastikan pemetaan flag→parameter benar.

**Opsi global muncul di dua tempat, dan itu disengaja.** Typer menaruh opsi
sebuah *group* hanya sebelum nama subcommand (``app --profile local doctor``),
padahal orang mengetik ``app doctor --profile local``. Daripada memaksa urutan
yang tidak wajar, opsi global dideklarasikan sekali sebagai alias ``Annotated``
lalu dipakai di callback **dan** di setiap perintah; :func:`_merge_globals`
yang menyelesaikan presedennya (nilai di subcommand menang atas callback).
Bantuan ``--help`` tiap perintah ikut menampilkannya — dan itu memang berguna.

**Callback root menjalankan ``run``.** Itulah yang membuat baris invokasi §42 —
``python -m app.main --rps input/rps/rps.tex --output output/book`` — bekerja
apa adanya, tanpa subcommand, dan tetap berarti hal yang sama dengan
``python -m app.main run ...``: satu jalur kode, bukan dua.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Optional

import typer

from app.commands import (
    DoctorParams,
    GlobalOptions,
    RunParams,
    do_doctor,
    do_export,
    do_ingest,
    do_plan,
    do_run,
    do_status,
    do_write_chapter,
)

# ---------------------------------------------------------------------------
# Alias opsi global — dideklarasikan sekali, dipakai di callback dan subcommand
# ---------------------------------------------------------------------------
ConfigOpt = Annotated[
    Optional[Path],
    typer.Option("--config", "-C", help="Berkas konfigurasi (default: config.yaml)."),
]
ProfileOpt = Annotated[
    Optional[str],
    typer.Option("--profile", "-p", help="Profil model: default atau local."),
]
SetModelOpt = Annotated[
    Optional[list[str]],
    typer.Option(
        "--set-model",
        metavar="PERAN=MODEL",
        help="Ganti model satu peran, dapat diulang. Contoh: --set-model writer=gemma3:4b",
    ),
]
VerboseOpt = Annotated[
    Optional[bool],
    typer.Option("--verbose", "-v", help="Tampilkan setiap tahap dan traceback penuh."),
]
LogJsonOpt = Annotated[
    Optional[bool],
    typer.Option("--log-json", help="Tulis log JSONL ke state/run.jsonl (§38)."),
]

# ---------------------------------------------------------------------------
# Alias opsi penyusunan buku — sama untuk `plan` dan `run` dengan sengaja:
# keduanya harus menghasilkan BookRequest yang identik dari flag yang sama.
# ---------------------------------------------------------------------------
RpsOpt = Annotated[
    Optional[Path],
    typer.Option("--rps", "-r", help="Berkas RPS (teks) yang menjadi dasar buku."),
]
ReferencesOpt = Annotated[
    Optional[Path],
    typer.Option("--references", help="Direktori bahan rujukan (default: input/references/)."),
]
LatexTemplateOpt = Annotated[
    Optional[Path],
    typer.Option("--latex-template", help="Direktori template LaTeX. [belum diimplementasikan]"),
]
OutputOpt = Annotated[
    Optional[Path],
    typer.Option("--output", "-o", help="Direktori keluaran (default: output/)."),
]
TitleOpt = Annotated[
    Optional[str],
    typer.Option("--title", help="Judul buku. Bila kosong, diambil dari nama berkas RPS."),
]
AudienceOpt = Annotated[
    Optional[str],
    typer.Option("--audience", help="Sasaran pembaca (default: mahasiswa S1)."),
]
LanguageOpt = Annotated[
    Optional[str],
    typer.Option("--language", help="Kode bahasa keluaran (default: id)."),
]
TargetChaptersOpt = Annotated[
    Optional[int],
    typer.Option("--chapters", "-n", help="Jumlah bab yang direncanakan (default dari config)."),
]
MaxRevisionsOpt = Annotated[
    Optional[int],
    typer.Option("--max-revisions", help="Batas revisi per bab (default dari config)."),
]
ChapterOpt = Annotated[
    Optional[int],
    typer.Option("--chapter", "-c", help="Kerjakan hanya bab ini."),
]
FromOpt = Annotated[
    Optional[int],
    typer.Option("--from", help="Mulai dari bab ini."),
]
ToOpt = Annotated[
    Optional[int],
    typer.Option("--to", help="Sampai bab ini."),
]
ForceOpt = Annotated[
    Optional[bool],
    typer.Option("--force", help="Kerjakan ulang bab yang sudah disetujui."),
]
DryRunOpt = Annotated[
    Optional[bool],
    typer.Option("--dry-run", help="Tulis prompt ke state/dryrun/, tanpa memanggil model."),
]

app = typer.Typer(
    name="ai-book",
    help="AI Agent Buku Ajar - pipeline penulisan buku ajar bab-per-bab.",
    no_args_is_help=True,
    add_completion=False,
)


# ---------------------------------------------------------------------------
# Penyiapan & pembantu
# ---------------------------------------------------------------------------
def configure_stdio() -> None:
    """Paksa ``stdout``/``stderr`` ke UTF-8 sebelum satu karakter pun ditulis.

    Bukan hiasan: konsol Windows warisan memakai cp1252, dan karakter seperti
    ``→`` atau ``·`` **mematikan perintah di tengah jalan** dengan
    ``UnicodeEncodeError`` — tepat saat sedang melaporkan tabel hasil.

    Diterapkan di titik masuk, bukan di reporter, karena hanya di sini kita
    tahu ini proses terminal. Saat stream tidak punya ``reconfigure`` (mis.
    di dalam pytest atau saat output dialihkan), tidak ada yang dilakukan —
    dan itu benar.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - stream tanpa buffer
            continue


def _merge_globals(
    ctx: typer.Context,
    *,
    config: Path | None,
    profile: str | None,
    set_model: list[str] | None,
    verbose: bool | None,
    log_json: bool | None,
) -> GlobalOptions:
    """Gabungkan opsi global dari callback root dan dari subcommand.

    Nilai yang diberikan di subcommand **menang**; yang tidak diberikan jatuh
    ke nilai callback. Inilah yang membuat ``app --profile local doctor`` dan
    ``app doctor --profile local`` sama-sama benar.
    """
    base = ctx.obj if isinstance(ctx.obj, GlobalOptions) else GlobalOptions()
    return GlobalOptions(
        config_path=config if config is not None else base.config_path,
        profile=profile if profile is not None else base.profile,
        set_model=tuple(set_model) if set_model else base.set_model,
        verbose=verbose if verbose is not None else base.verbose,
        log_json=log_json if log_json is not None else base.log_json,
    )


def _params(
    ctx: typer.Context,
    *,
    config: Path | None,
    profile: str | None,
    set_model: list[str] | None,
    verbose: bool | None,
    log_json: bool | None,
    rps: Path | None = None,
    references: Path | None = None,
    latex_template: Path | None = None,
    output: Path | None = None,
    title: str = "",
    audience: str = "",
    language: str | None = None,
    target_chapters: int | None = None,
    max_revisions: int | None = None,
    chapter: int | None = None,
    chapters_from: int | None = None,
    chapters_to: int | None = None,
    force: bool = False,
    dry_run: bool = False,
    latex: bool = False,
) -> RunParams:
    """Bangun :class:`~app.commands.RunParams` dari opsi yang sudah di-parse.

    Seluruh ``None`` dan ``""`` diteruskan apa adanya, bukan diganti default di
    sini: "tidak disebut" dan "disebut sebagai nilai bawaan" harus tetap dapat
    dibedakan, karena hanya yang pertama yang jatuh ke ``config.yaml``.
    """
    return RunParams(
        globals=_merge_globals(
            ctx,
            config=config,
            profile=profile,
            set_model=set_model,
            verbose=verbose,
            log_json=log_json,
        ),
        rps=rps,
        references=references,
        latex_template=latex_template,
        output=output,
        title=title or "",
        audience=audience or "",
        language=language,
        target_chapters=target_chapters,
        max_revisions=max_revisions,
        chapter=chapter,
        chapters_from=chapters_from,
        chapters_to=chapters_to,
        force=bool(force),
        dry_run=bool(dry_run),
        latex=bool(latex),
    )


# ---------------------------------------------------------------------------
# Callback root (§42: tanpa subcommand, opsi di sini menjalankan `run`)
# ---------------------------------------------------------------------------
@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    config: ConfigOpt = None,
    profile: ProfileOpt = None,
    set_model: SetModelOpt = None,
    verbose: VerboseOpt = None,
    log_json: LogJsonOpt = None,
    rps: RpsOpt = None,
    references: ReferencesOpt = None,
    latex_template: LatexTemplateOpt = None,
    output: OutputOpt = None,
    title: TitleOpt = None,
    audience: AudienceOpt = None,
    language: LanguageOpt = None,
    target_chapters: TargetChaptersOpt = None,
    max_revisions: MaxRevisionsOpt = None,
    chapter: ChapterOpt = None,
    from_chapter: FromOpt = None,
    to_chapter: ToOpt = None,
    force: ForceOpt = None,
    dry_run: DryRunOpt = None,
) -> None:
    """Simpan opsi global; tanpa subcommand, jalankan ``run`` (§42)."""
    ctx.obj = GlobalOptions(
        config_path=config,
        profile=profile,
        set_model=tuple(set_model or ()),
        verbose=bool(verbose),
        log_json=bool(log_json),
    )

    if ctx.invoked_subcommand is not None:
        return

    params = _params(
        ctx,
        config=config,
        profile=profile,
        set_model=set_model,
        verbose=verbose,
        log_json=log_json,
        rps=rps,
        references=references,
        latex_template=latex_template,
        output=output,
        title=title or "",
        audience=audience or "",
        language=language,
        target_chapters=target_chapters,
        max_revisions=max_revisions,
        chapter=chapter,
        chapters_from=from_chapter,
        chapters_to=to_chapter,
        force=bool(force),
        dry_run=bool(dry_run),
    )
    raise typer.Exit(code=do_run(params))


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------
@app.command()
def doctor(
    ctx: typer.Context,
    config: ConfigOpt = None,
    profile: ProfileOpt = None,
    set_model: SetModelOpt = None,
    verbose: VerboseOpt = None,
    log_json: LogJsonOpt = None,
    probe: Annotated[
        bool,
        typer.Option(
            "--probe/--no-probe",
            help="Kirim 1 token ke setiap model untuk memastikan ia benar-benar menjawab.",
        ),
    ] = True,
) -> None:
    """Periksa konfigurasi, server Ollama, dan keterjangkauan setiap peran."""
    globals_ = _merge_globals(
        ctx,
        config=config,
        profile=profile,
        set_model=set_model,
        verbose=verbose,
        log_json=log_json,
    )
    raise typer.Exit(code=do_doctor(DoctorParams(globals=globals_, probe=probe)))


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------
@app.command()
def plan(
    ctx: typer.Context,
    config: ConfigOpt = None,
    profile: ProfileOpt = None,
    set_model: SetModelOpt = None,
    verbose: VerboseOpt = None,
    log_json: LogJsonOpt = None,
    rps: RpsOpt = None,
    references: ReferencesOpt = None,
    latex_template: LatexTemplateOpt = None,
    output: OutputOpt = None,
    title: TitleOpt = None,
    audience: AudienceOpt = None,
    language: LanguageOpt = None,
    target_chapters: TargetChaptersOpt = None,
    max_revisions: MaxRevisionsOpt = None,
) -> None:
    """Susun BookSpec dari RPS dan simpan ke state/book.json."""
    raise typer.Exit(
        code=do_plan(
            _params(
                ctx,
                config=config,
                profile=profile,
                set_model=set_model,
                verbose=verbose,
                log_json=log_json,
                rps=rps,
                references=references,
                latex_template=latex_template,
                output=output,
                title=title or "",
                audience=audience or "",
                language=language,
                target_chapters=target_chapters,
                max_revisions=max_revisions,
            )
        )
    )


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------
@app.command()
def run(
    ctx: typer.Context,
    config: ConfigOpt = None,
    profile: ProfileOpt = None,
    set_model: SetModelOpt = None,
    verbose: VerboseOpt = None,
    log_json: LogJsonOpt = None,
    rps: RpsOpt = None,
    references: ReferencesOpt = None,
    latex_template: LatexTemplateOpt = None,
    output: OutputOpt = None,
    title: TitleOpt = None,
    audience: AudienceOpt = None,
    language: LanguageOpt = None,
    target_chapters: TargetChaptersOpt = None,
    max_revisions: MaxRevisionsOpt = None,
    chapter: ChapterOpt = None,
    from_chapter: FromOpt = None,
    to_chapter: ToOpt = None,
    force: ForceOpt = None,
    dry_run: DryRunOpt = None,
) -> None:
    """Kerjakan seluruh bab yang belum selesai (resume secara bawaan)."""
    raise typer.Exit(
        code=do_run(
            _params(
                ctx,
                config=config,
                profile=profile,
                set_model=set_model,
                verbose=verbose,
                log_json=log_json,
                rps=rps,
                references=references,
                latex_template=latex_template,
                output=output,
                title=title or "",
                audience=audience or "",
                language=language,
                target_chapters=target_chapters,
                max_revisions=max_revisions,
                chapter=chapter,
                chapters_from=from_chapter,
                chapters_to=to_chapter,
                force=bool(force),
                dry_run=bool(dry_run),
            )
        )
    )


# ---------------------------------------------------------------------------
# write-chapter
# ---------------------------------------------------------------------------
@app.command(name="write-chapter")
def write_chapter(
    ctx: typer.Context,
    number: Annotated[int, typer.Argument(help="Nomor bab yang dikerjakan.")],
    config: ConfigOpt = None,
    profile: ProfileOpt = None,
    set_model: SetModelOpt = None,
    verbose: VerboseOpt = None,
    log_json: LogJsonOpt = None,
    output: OutputOpt = None,
    max_revisions: MaxRevisionsOpt = None,
    force: ForceOpt = None,
    dry_run: DryRunOpt = None,
) -> None:
    """Kerjakan satu bab sampai tuntas, atau sampai ia menyerah."""
    raise typer.Exit(
        code=do_write_chapter(
            number,
            _params(
                ctx,
                config=config,
                profile=profile,
                set_model=set_model,
                verbose=verbose,
                log_json=log_json,
                output=output,
                max_revisions=max_revisions,
                force=bool(force),
                dry_run=bool(dry_run),
            ),
        )
    )


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------
@app.command()
def ingest(
    ctx: typer.Context,
    config: ConfigOpt = None,
    profile: ProfileOpt = None,
    set_model: SetModelOpt = None,
    verbose: VerboseOpt = None,
    log_json: LogJsonOpt = None,
    references: ReferencesOpt = None,
) -> None:
    """Bangun ulang basis pengetahuan turunan: indeks vektor dan graf konsep (§10, §13, §14)."""
    raise typer.Exit(
        code=do_ingest(
            _params(
                ctx,
                config=config,
                profile=profile,
                set_model=set_model,
                verbose=verbose,
                log_json=log_json,
                references=references,
            )
        )
    )


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
@app.command()
def status(
    ctx: typer.Context,
    config: ConfigOpt = None,
    profile: ProfileOpt = None,
    set_model: SetModelOpt = None,
    verbose: VerboseOpt = None,
    log_json: LogJsonOpt = None,
    output: OutputOpt = None,
) -> None:
    """Tampilkan status setiap bab dan model yang dipakai tiap peran."""
    raise typer.Exit(
        code=do_status(
            _params(
                ctx,
                config=config,
                profile=profile,
                set_model=set_model,
                verbose=verbose,
                log_json=log_json,
                output=output,
            )
        )
    )


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------
@app.command(name="export")
def export(
    ctx: typer.Context,
    config: ConfigOpt = None,
    profile: ProfileOpt = None,
    set_model: SetModelOpt = None,
    verbose: VerboseOpt = None,
    log_json: LogJsonOpt = None,
    output: OutputOpt = None,
    latex: bool = typer.Option(
        False,
        "--latex",
        help="Rakit dan kompilasi buku LaTeX menjadi output/latex/book.pdf (§42).",
    ),
) -> None:
    """Gabungkan bab yang sudah disetujui menjadi output/book.md.

    Dengan ``--latex``, buku LaTeX-nya ikut dirakit dan dikompilasi.
    """
    raise typer.Exit(
        code=do_export(
            _params(
                ctx,
                config=config,
                profile=profile,
                set_model=set_model,
                verbose=verbose,
                log_json=log_json,
                output=output,
                latex=latex,
            )
        )
    )


def main() -> None:
    """Titik masuk ``ai-book`` (lihat ``entry_points`` di ``setup.cfg``)."""
    configure_stdio()
    app()


__all__ = [
    "app",
    "configure_stdio",
    "doctor",
    "export",
    "ingest",
    "main",
    "plan",
    "root",
    "run",
    "status",
    "write_chapter",
]
