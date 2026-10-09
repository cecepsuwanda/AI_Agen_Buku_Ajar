"""CLI diuji lewat antarmuka aslinya, seluruhnya tanpa jaringan (§37, §42).

Yang dijaga berkas ini adalah **pemetaan flag → parameter**, bukan logika
bisnisnya. Apakah ``--rps`` sampai ke pembaca berkas, apakah ``--chapter 99``
ditolak dengan menyebut bab yang benar-benar ada, apakah kegagalan datang
sebagai kalimat atau sebagai traceback. Pipelinenya sendiri sudah diuji di
``test_orchestrator_offline.py``; mengulanginya di sini hanya akan membuat dua
tempat yang harus dijaga sinkron.

Tiga invarian yang tidak dapat dibuktikan di tempat lain:

1. **Callback root benar-benar ``run``** (§42). Baris invokasi blueprint tidak
   punya subcommand, dan tes ini membuktikan keduanya menghasilkan artefak yang
   **identik** — bukan sekadar sama-sama berhasil. Kalau kelak seseorang menambah
   perilaku pada salah satu jalur saja, di sinilah ia ketahuan.
2. **``--dry-run`` tidak membuka soket.** Dibuktikan, bukan diasumsikan:
   ``BUKUAJAR_OLLAMA_BASE_URL`` diarahkan ke port mati, jadi satu panggilan
   jaringan yang tidak disengaja akan menggagalkan tes ini alih-alih lolos
   diam-diam karena Ollama kebetulan sedang berjalan.
3. **``state/`` dan ``output/`` repo tidak pernah tersentuh.** Seluruh tes
   mengarahkan keduanya ke ``tmp_path`` lewat preseden ``BUKUAJAR_*`` (§28).
   Suite yang mengubah deliverable yang di-track git adalah suite yang tidak
   boleh dijalankan siapa pun.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.cli import app
from app.container import SANDBOX_DIRNAME, FixedClock
from domain.book import BookRequest, BookSpec, ChapterSpec
from domain.chapter import ChapterDraft, ChapterRecord, ReviewResult, Section
from domain.enums import ChapterStatus
from domain.state import BookState
from memory.project_state import JsonStateStore
from tests.conftest import PROJECT_ROOT

#: RPS contoh yang di-commit ke repo, dipakai apa adanya. Bukan berkas buatan
#: tes: yang diuji adalah jalur yang benar-benar dipakai orang, dan isi RPS tidak
#: memengaruhi hasil dry-run (balasan disintesis dari skema), sehingga berkas
#: kecil buatan tes hanya akan membuktikan lebih sedikit.
RPS_PATH = PROJECT_ROOT / "input" / "rps" / "rps.tex"

#: Port yang tidak mungkin menjawab.
DEAD_ENDPOINT = "http://127.0.0.1:1"

RUNNER = CliRunner()


@dataclass(frozen=True)
class Layout:
    """Direktori sementara untuk satu jalankan CLI."""

    root: Path
    state: Path
    output: Path

    @property
    def sandbox(self) -> Path:
        """Akar sandbox ``--dry-run`` (``state/dryrun``) — tempat prompt ditulis."""
        return self.state / SANDBOX_DIRNAME

    @property
    def sandbox_state(self) -> Path:
        """``state/`` milik dry-run — bukan ``state/`` sungguhan."""
        return self.sandbox / "state"

    @property
    def sandbox_output(self) -> Path:
        """``output/`` milik dry-run — bukan ``output/`` sungguhan."""
        return self.sandbox / "output"

    def files(self) -> set[str]:
        """Seluruh berkas di bawah ``state/`` dan ``output/``, sebagai jalur relatif.

        Dipakai untuk membandingkan dua jalankan. Isi berkasnya tidak
        dibandingkan karena ``state/*.json`` memuat cap waktu; yang dibandingkan
        adalah **apa yang dihasilkan**, dan itu sudah cukup untuk membuktikan
        kedua jalur mengerjakan pekerjaan yang sama.
        """
        found: set[str] = set()
        for base in (self.state, self.output):
            if base.is_dir():
                found |= {
                    f"{base.name}/{path.relative_to(base).as_posix()}"
                    for path in base.rglob("*")
                    if path.is_file()
                }
        return found


def _layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str = "ws") -> Layout:
    """Arahkan ``state/`` dan ``output/`` ke ``tmp_path`` lewat preseden env.

    Env, bukan ``--config``: yang sedang diuji adalah perintahnya, dan tes ini
    tidak perlu menyusun berkas konfigurasi tiruan untuk menguji pemetaan flag.
    """
    root = tmp_path / name
    monkeypatch.setenv("BUKUAJAR_STATE_DIR", str(root / "state"))
    monkeypatch.setenv("BUKUAJAR_OUTPUT_DIR", str(root / "output"))
    return Layout(root=root, state=root / "state", output=root / "output")


def _dry_run_prompts(layout: Layout) -> dict[str, str]:
    """Berkas prompt ``--dry-run``, dipetakan nama berkas → isi."""
    if not layout.sandbox.is_dir():
        return {}
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(layout.sandbox.glob("*.txt"))
    }


def _role_of(filename: str) -> str:
    """Peran dari nama berkas prompt, mis. ``02-reviewer.txt`` → ``"reviewer"``.

    Bentuk namanya milik :data:`models.dry_run.FILE_TEMPLATE`; diurai di sini alih-alih
    diimpor supaya tes ini tetap menyatakan harapannya sendiri, bukan menurunkan
    harapan itu dari kode yang sedang diuji.
    """
    return filename[3:-4]


def _model_line(prompt: str) -> str:
    """Nama model yang tertulis di kepala sebuah berkas prompt ``--dry-run``."""
    for line in prompt.splitlines():
        if line.startswith("# model"):
            return line.split(":", 1)[1].strip()
    return ""


def _seed_plan(layout: Layout, *, chapters: int = 3) -> BookSpec:
    """Tulis rencana buku langsung ke state, tanpa LLM.

    Bukan lewat ``run --dry-run``: hasil dry-run sengaja dikurung di sandbox dan
    sandbox dibersihkan di awal setiap jalankan, jadi rencana yang disemai dengan
    cara itu lenyap sebelum tes sempat memakainya. Yang diuji di sini adalah
    perilaku terhadap buku yang **sudah** direncanakan, jadi rencananya ditulis ke
    tempat yang memang dibaca perintah: ``state/``.
    """
    store = JsonStateStore(layout.state, clock=FixedClock("2026-10-07T12:00:00+00:00"))
    spec = BookSpec(
        title="Buku Uji",
        chapters=tuple(
            ChapterSpec(
                number=number,
                title=f"Bab {number}",
                objectives=(f"memahami hal {number}",),
                sections=("Pengantar",),
            )
            for number in range(1, chapters + 1)
        ),
    )
    store.save_book(
        BookState(request=BookRequest(title="Buku Uji", target_chapters=chapters), spec=spec)
    )
    return spec


# ---------------------------------------------------------------------------
# Bantuan
# ---------------------------------------------------------------------------
def test_help_lists_every_command() -> None:
    """``--help`` menampilkan seluruh perintah dan opsi penyusunan buku."""
    result = RUNNER.invoke(app, ["--help"])

    assert result.exit_code == 0, result.output
    for command in (
        "doctor",
        "plan",
        "run",
        "write-chapter",
        "status",
        "export",
        "compare",
        "ingest",
        "approve",
        "reject",
    ):
        assert command in result.output, command
    for option in ("--rps", "--output", "--dry-run", "--set-model", "--profile"):
        assert option in result.output, option


def test_root_callback_is_the_same_path_as_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§42: tanpa subcommand, hasilnya identik dengan ``run`` — bukan mirip.

    Yang dibandingkan adalah berkas yang dihasilkan **dan** isi prompt dry-run-nya.
    Isi prompt ikut dibandingkan karena ia memuat rantai peran yang benar-benar
    dipanggil, urutan parameter, dan tanda versi prompt (§38) — tiga hal yang
    akan berbeda bila jalur root ternyata merakit konteks yang berbeda.
    """
    root_side = _layout(tmp_path, monkeypatch, "root")
    via_root = RUNNER.invoke(
        app,
        ["--dry-run", "--rps", str(RPS_PATH), "--output", str(root_side.output)],
    )
    assert via_root.exit_code == 0, via_root.output

    run_side = _layout(tmp_path, monkeypatch, "run")
    via_run = RUNNER.invoke(
        app,
        ["run", "--dry-run", "--rps", str(RPS_PATH), "--output", str(run_side.output)],
    )
    assert via_run.exit_code == 0, via_run.output

    assert root_side.files() == run_side.files()
    assert _dry_run_prompts(root_side) == _dry_run_prompts(run_side)
    assert _sandbox_markdown(root_side) == _sandbox_markdown(run_side)


# ---------------------------------------------------------------------------
# run --dry-run: pipeline sungguhan, nol jaringan
# ---------------------------------------------------------------------------
def test_dry_run_covers_the_whole_pipeline_without_a_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Baris verifikasi rencana: prompt ditulis ke state/dryrun/, nol chat, exit 0.

    ``BUKUAJAR_OLLAMA_BASE_URL`` yang mati adalah bagian dari asersinya, bukan
    hiasan: tanpa itu, tes ini akan tetap hijau meskipun ``--dry-run`` diam-diam
    mengirim satu permintaan ke Ollama yang kebetulan sedang berjalan.
    """
    layout = _layout(tmp_path, monkeypatch)
    monkeypatch.setenv("BUKUAJAR_OLLAMA_BASE_URL", DEAD_ENDPOINT)

    result = RUNNER.invoke(
        app, ["run", "--dry-run", "--rps", str(RPS_PATH), "--output", str(layout.output)]
    )

    assert result.exit_code == 0, result.output

    # Rantai peran yang benar-benar dipanggil, berurutan. Seluruh jalur berperan
    # hadir di sini — dan itulah gunanya: ``--dry-run`` adalah satu-satunya cara
    # membuktikan setiap agent benar-benar dipanggil, tanpa jaringan dan tanpa
    # menghitung panggilan model.
    #
    # ``04-code`` dan ``05-writer`` adalah dua tahap penulisan §19/§20 yang
    # berjalan SESUDAH penulis draf. Peran ``code`` dipakai Example Agent karena
    # contoh butuh ketepatan sintaks; peran ``writer`` dipakai Exercise Agent
    # karena latihan adalah prosa. Nomor berkasnyalah yang membuktikan keduanya
    # berada di antara penulis dan peninjau — bukan sebelum, bukan sesudah.
    #
    # ``06-reviewer``, ``07-reviewer``, lalu ``08-reviewer``: TIGA gate yang
    # berbeda memakai peran yang sama, dan urutannya menentukan perilakunya.
    # Yang pertama ``pedagogy_reviewer`` (§23), yang berdiri SESUDAH contoh dan
    # latihan — tanpa keduanya tidak ada yang dapat dinilai tentang keduanya.
    # Yang kedua ``consistency_checker`` (§24), satu-satunya gate yang membaca bab
    # terhadap SELURUH buku; dialah yang mencatat istilah bab ini ke
    # ``BookState.terminology``, sehingga urutannya menentukan apakah bab ke-7
    # dapat melihat istilah bab ke-2. Yang ketiga peninjau akhir (§27), dan ia
    # sengaja yang terakhir di antara ketiganya: keputusannya adalah keputusan
    # sistem, yang seharusnya melihat bab setelah pedagogi dan konsistensinya
    # dinilai. Nomor berkasnyalah yang membuktikan ketiga urutan itu.
    #
    # ``09-latex``: §25 berada SESUDAH peninjau, sesuai rantai §27
    # (LATEX_GENERATED setelah REVIEWED).
    #
    # ``10-latex`` dan ``11-latex`` bukan pengulangan yang tidak disengaja.
    # ``SchemaEchoChatModel`` menyintesis bab dari skema saja — ia tidak melihat
    # daftar kunci sitasi yang diberikan, jadi ``citations`` yang disintesiskannya
    # tidak pernah muncul di ``body_tex``, dan tangga perbaikan §25 berjalan
    # sampai habis. Model sungguhan menerima daftar kunci itu di dalam prompt dan
    # hampir selalu bersih pada percobaan pertama; yang terlihat di sini adalah
    # **jalur gagal**, dan justru itu yang layak dibuktikan tanpa jaringan.
    prompts = _dry_run_prompts(layout)
    assert set(prompts) == {
        "01-planner.txt",
        "02-chapter_planner.txt",
        "03-writer.txt",
        "04-code.txt",
        "05-writer.txt",
        "06-reviewer.txt",
        "07-reviewer.txt",
        "08-reviewer.txt",
        "09-latex.txt",
        "10-latex.txt",
        "11-latex.txt",
    }

    # Setiap prompt memuat system, user, skema format= mentah, dan model+opsi
    # efektif peran itu — yang terakhir inilah yang tidak dapat dibaca dari
    # prompt mana pun.
    planner = prompts["01-planner.txt"]
    assert "SYSTEM" in planner
    assert "USER" in planner
    assert "FORMAT (JSON Schema yang membatasi decoding)" in planner
    assert "# peran   : planner" in planner
    assert "# model   : " in planner and "# model   : (tidak dipetakan)" not in planner
    assert "# opsi    : (bawaan server)" not in planner
    assert "versi 0+" not in planner  # §38: versi prompt harus terisi, bukan nol

    # §12: yang sampai ke perencana adalah RPS yang sudah **diuraikan**, bukan
    # berkas mentahnya — nomor minggu disebut eksplisit di sana, minggu ujian
    # ditandai, dan komentar penulis berkas RPS tidak ikut terbawa sebagai isi.
    assert "## Kalender Mingguan" in planner
    assert "- Minggu 9: Pohon seimbang" in planner
    assert "[MINGGU PENILAIAN — bukan bahan bab]" in planner
    assert "menyusul di Tahap" not in planner, "komentar RPS bukan isi RPS"

    # Perincian per bab (§16) benar-benar diminta, dan memakai modelnya sendiri:
    # setiap peran dipetakan ke model oleh config, bukan oleh agent (§6).
    chapter_planner = prompts["02-chapter_planner.txt"]
    assert "# peran   : chapter_planner" in chapter_planner
    assert _model_line(chapter_planner) not in ("", "(tidak dipetakan)")
    assert _model_line(chapter_planner) != _model_line(planner)

    # Rencana tersusun otomatis: baris invokasi §42 tidak menuntut 'plan' lebih dulu.
    assert (layout.sandbox_state / "book.json").is_file()

    # §12: ``source_weeks`` benar-benar terisi, dan diisi dari kalender RPS —
    # bukan dari keluaran perencana. Model dry-run menyintesis BookSpec dari
    # skema, jadi ia tidak menghasilkan satu pun minggu; tanpa aturan pemetaan,
    # bab ini akan lolos ke penulisan tanpa menunjuk materi mana pun. Yang
    # muncul adalah seluruh minggu kuliah RPS, dengan minggu 8 (ujian tengah
    # semester) di luar rentangnya.
    book = json.loads((layout.sandbox_state / "book.json").read_text(encoding="utf-8"))
    assert book["spec"]["chapters"][0]["source_weeks"] == ["Minggu 1-7, 9-15"]

    chapter = json.loads((layout.sandbox_state / "chapter01.json").read_text(encoding="utf-8"))
    assert chapter["status"] == "APPROVED"
    assert chapter["markdown_path"]

    markdown = _sandbox_markdown(layout)
    assert markdown.startswith("# Bab 1.")
    assert "## Contoh" in markdown
    assert "## Latihan" in markdown


def test_an_rps_of_another_shape_falls_back_to_raw_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RPS yang tidak dapat diuraikan tetap dikirim apa adanya, dengan peringatan.

    Ini perilaku yang **sama seperti sebelum pengurai ada** — dan itu disengaja.
    Pengurai yang menghentikan pipeline karena berkas RPS-nya berbentuk lain
    adalah kemunduran: pengguna yang RPS-nya ditulis di Word lalu diekspor ke
    teks tetap harus mendapat buku. Yang tidak boleh terjadi hanyalah ia tidak
    tahu bahwa penguraiannya meleset, sebab ketika itu pemetaan minggu ikut mati
    dan tidak ada lagi yang memeriksa bab mana menunjuk materi mana.
    """
    layout = _layout(tmp_path, monkeypatch)
    foreign = tmp_path / "rps-lain.txt"
    foreign.write_text(
        "Mata kuliah ini membahas jaringan komputer.\nPertemuan pertama: pengantar.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("BUKUAJAR_OLLAMA_BASE_URL", DEAD_ENDPOINT)

    result = RUNNER.invoke(
        app, ["run", "--dry-run", "--rps", str(foreign), "--output", str(layout.output)]
    )

    assert result.exit_code == 0, result.output
    assert "Catatan penguraian RPS" in result.output
    assert "tidak memuat satu pun" in result.output

    # Isi berkasnya sampai ke perencana — mentah, karena tidak ada ringkasan
    # yang dapat dibuat darinya.
    planner = _dry_run_prompts(layout)["01-planner.txt"]
    assert "membahas jaringan komputer" in planner
    assert "## Kalender Mingguan" not in planner

    # Dan tidak ada minggu yang dikarang: tanpa kalender, pemetaan minggunya
    # dibiarkan apa adanya — keluaran perencana, apa pun isinya — alih-alih diisi
    # label minggu yang tidak ada yang dapat memeriksa kebenarannya.
    book = json.loads((layout.sandbox_state / "book.json").read_text(encoding="utf-8"))
    assert book["spec"]["chapters"]
    assert not any(
        label.lower().startswith("minggu")
        for chapter in book["spec"]["chapters"]
        for label in chapter["source_weeks"]
    )


def test_dry_run_never_marks_a_chapter_done_for_real(tmp_path, monkeypatch) -> None:
    """Hasil dry-run tidak boleh tercatat sebagai bab yang sudah disetujui (§28).

    Inilah bahaya yang membuat sandbox ada, dan bentuknya halus: ``--dry-run``
    menjalankan pipeline sungguhan, jadi ia menandai bab ``APPROVED`` — dan bab
    ``APPROVED`` **dilewati** saat resume. Bila hasilnya mendarat di ``state/``
    dan ``output/`` yang sungguhan, jalankan berikutnya melaporkan "1 bab
    dilewati karena sudah selesai" sambil menyerahkan teks sintetis dari
    ``--dry-run`` sebagai bab jadi — tanpa satu pun tanda bahwa itu terjadi.

    Karena itu dua asersi di bawah ini setara pentingnya: sandbox berisi sesuatu,
    dan direktori sungguhan **tidak** berisi apa-apa.
    """
    layout = _layout(tmp_path, monkeypatch)

    result = RUNNER.invoke(
        app, ["run", "--dry-run", "--rps", str(RPS_PATH), "--output", str(layout.output)]
    )

    assert result.exit_code == 0, result.output
    assert (layout.sandbox_state / "book.json").is_file()
    assert (layout.sandbox_output / "chapters" / "chapter01.md").is_file()

    assert _files_under(layout.state, exclude=SANDBOX_DIRNAME) == set()
    assert _files_under(layout.output) == set()


def test_dry_run_writes_the_jsonl_run_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--log-json`` menulis satu baris JSONL yang dapat dibaca mesin (§38)."""
    layout = _layout(tmp_path, monkeypatch)

    result = RUNNER.invoke(
        app,
        [
            "run",
            "--dry-run",
            "--log-json",
            "--rps",
            str(RPS_PATH),
            "--output",
            str(layout.output),
        ],
    )

    assert result.exit_code == 0, result.output
    lines = (layout.sandbox_state / "run.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["approved"] == 1
    assert record["total"] == 1
    assert record["chapters"][0]["number"] == 1
    assert record["chapters"][0]["status"] == "APPROVED"
    # Model per peran ikut tercatat: tanpa itu, membandingkan mutu antar-model
    # tidak mungkin dilakukan setelah fakta.
    assert record["models"]["writer"]


def test_a_run_writes_the_per_role_call_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Setiap panggilan model meninggalkan satu baris di ``output/logs/<peran>.jsonl`` (§38).

    Ini pembuktian bahwa pencatatnya benar-benar **terpasang** — bukan sekadar
    ada sebagai kelas yang diuji terpisah. Yang menghubungkannya adalah
    composition root, dan hanya jalur sungguhan yang dapat membuktikannya.

    ``--dry-run`` dipakai karena ia menempuh seluruh rantai gate tanpa satu
    token: yang sedang diuji adalah pemasangan pencatatnya, bukan mutu jawaban.
    """
    layout = _layout(tmp_path, monkeypatch)

    result = RUNNER.invoke(
        app, ["run", "--dry-run", "--rps", str(RPS_PATH), "--output", str(layout.output)]
    )

    assert result.exit_code == 0, result.output
    logs = layout.sandbox_output / "logs"
    written = sorted(path.name for path in logs.glob("*.jsonl"))
    assert "writer.jsonl" in written, f"catatan penulis harus ada, yang tertulis: {written}"

    record = json.loads((logs / "writer.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert record["role"] == "writer"
    assert record["model"], "nama model yang menjawab ikut tercatat (itulah yang dibandingkan §33)"


def test_the_call_log_never_leaks_into_the_repository(tmp_path: Path, monkeypatch) -> None:
    """Catatan §38 hidup di dalam sandbox saat dry-run — bukan di ``output/logs`` repo.

    Yang dituntut adalah **tidak ada baris catatan yang bocor**, bukan ketiadaan
    direktorinya. ``output/logs/`` adalah direktori runtime yang sah —
    :meth:`~app.container.Container.ensure_runtime_dirs` membuatnya untuk setiap
    jalankan, termasuk ``doctor`` — sehingga pengembang yang pernah menjalankan
    perintah yang didokumentasikan memang sudah memilikinya, dan menuntut
    direktori itu tidak ada akan membuat tes ini gagal justru karena pekerjaan
    yang benar. Yang tidak boleh terjadi adalah ``.jsonl`` di dalamnya.
    """
    layout = _layout(tmp_path, monkeypatch)

    result = RUNNER.invoke(
        app, ["run", "--dry-run", "--rps", str(RPS_PATH), "--output", str(layout.output)]
    )

    assert result.exit_code == 0, result.output
    repo_logs = PROJECT_ROOT / "output" / "logs"
    leaked = sorted(path.name for path in repo_logs.glob("*.jsonl")) if repo_logs.is_dir() else []
    assert not leaked, f"catatan §38 bocor ke repo: {leaked}"


def test_dry_run_does_not_touch_the_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tidak ada berkas baru di ``state/``/``output/`` repo — hanya di tmp_path."""
    layout = _layout(tmp_path, monkeypatch)
    before = _repository_runtime_files()

    result = RUNNER.invoke(
        app, ["run", "--dry-run", "--rps", str(RPS_PATH), "--output", str(layout.output)]
    )

    assert result.exit_code == 0, result.output
    assert layout.files(), "dry-run harus menghasilkan sesuatu, di tmp_path"
    assert _repository_runtime_files() == before


def test_dry_run_on_a_planned_book_mirrors_the_plan_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--dry-run`` pada buku yang sudah direncanakan: jalan, dan tidak menulis apa pun.

    Dua hal yang dijaga di sini, keduanya perilaku yang mudah hilang diam-diam:

    * ``--rps`` **tidak** dituntut. Buku ini sudah punya rencana, dan dry-run
      membacanya lewat salinan — bukan dengan menuntut pengguna menyusun ulang.
    * Sandbox dimulai bersih. Berkas prompt bernomor urut dari 1 di setiap proses,
      jadi tanpa pembersihan, jalankan kedua akan meninggalkan ``01-writer.txt``
      dari jalankan pertama di sebelah berkas baru dengan nomor yang sama.
    """
    layout = _layout(tmp_path, monkeypatch)
    _seed_approved_chapter(layout)
    before = (layout.state / "book.json").read_text(encoding="utf-8")

    first = RUNNER.invoke(app, ["run", "--dry-run"])
    assert first.exit_code == 0, first.output
    first_prompts = set(_dry_run_prompts(layout))
    assert first_prompts, "dry-run pada buku yang sudah direncanakan harus tetap menulis prompt"

    second = RUNNER.invoke(app, ["run", "--dry-run"])
    assert second.exit_code == 0, second.output

    # Rencana tidak pernah disusun ulang: ia dibaca dari salinan, bukan dari model.
    # Perencana **buku** karena itu tidak dipanggil — tetapi perincian per bab tetap
    # dikerjakan, sebab ia bekerja pada bab, bukan pada buku (§16), dan tahap
    # contoh (§19), latihan (§20), pemeriksaan konsistensi (§24), serta LaTeX (§25)
    # ikut berjalan karena keempatnya juga bekerja pada bab.
    assert {_role_of(name) for name in _dry_run_prompts(layout)} == {
        "chapter_planner",
        "code",
        "writer",
        "reviewer",
        "latex",
    }
    # Tidak ada penumpukan antar-jalankan: sandbox dimulai bersih setiap kali.
    assert set(_dry_run_prompts(layout)) == first_prompts
    # Dan state sungguhan sama sekali tidak tersentuh.
    assert (layout.state / "book.json").read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# Kegagalan: kalimat yang menunjuk tindakan, bukan traceback
# ---------------------------------------------------------------------------
def test_run_without_plan_or_rps_explains_the_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tanpa rencana dan tanpa ``--rps``, yang salah adalah urutan perintahnya."""
    _layout(tmp_path, monkeypatch)

    result = RUNNER.invoke(app, ["run", "--dry-run"])

    assert result.exit_code == 1
    assert "Belum ada rencana buku" in result.output
    assert "plan" in result.output


def test_missing_rps_points_at_the_flag_that_was_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RPS yang salah jalur adalah kesalahan pengetikan, bukan konfigurasi rusak."""
    _layout(tmp_path, monkeypatch)

    result = RUNNER.invoke(
        app, ["run", "--dry-run", "--rps", str(tmp_path / "tidak-ada.tex")]
    )

    assert result.exit_code == 1
    assert "RPS" in result.output
    assert "--rps" in result.output or "tidak-ada.tex" in result.output


def test_narrowing_the_gate_chain_with_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--gates reviewer`` menjalankan satu gate saja, bukan rantai penuh (§27).

    Yang dibuktikan adalah **biaya**, bukan tampilan: rantai penuh berisi
    sembilan gate dan setiap revisi menjalankannya ulang, jadi kemampuan
    mempersempitnya adalah penangkal biaya token yang sesungguhnya. Buktinya
    diambil dari prompt yang benar-benar ditulis ``--dry-run`` — yaitu peran
    yang benar-benar dipanggil, bukan gate yang sekadar terdaftar.
    """
    layout = _layout(tmp_path, monkeypatch)
    _seed_plan(layout)

    result = RUNNER.invoke(app, ["run", "--dry-run", "--gates", "reviewer"])

    assert result.exit_code == 0, result.output
    assert {_role_of(name) for name in _dry_run_prompts(layout)} == {
        "chapter_planner",
        "writer",
        "reviewer",
    }


def test_an_empty_gates_value_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rantai tanpa gate menulis bab yang tidak diperiksa siapa pun.

    Menerimanya diam-diam akan jauh lebih buruk daripada menolaknya: hasilnya
    terlihat seperti buku yang selesai, dan tidak ada satu pun tanda bahwa
    seluruh pemeriksaan terlewat.
    """
    layout = _layout(tmp_path, monkeypatch)
    _seed_plan(layout)

    result = RUNNER.invoke(app, ["run", "--dry-run", "--gates", " , "])

    assert result.exit_code == 1
    assert "--gates" in result.output


def test_an_unknown_gate_name_is_refused_with_the_known_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nama gate yang salah ketik disebutkan, beserta yang benar-benar ada."""
    layout = _layout(tmp_path, monkeypatch)
    _seed_plan(layout)

    result = RUNNER.invoke(app, ["run", "--dry-run", "--gates", "penyair"])

    assert result.exit_code == 1
    assert "penyair" in result.output
    assert "reviewer" in result.output


def test_malformed_set_model_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--set-model`` tanpa ``=`` ditolak sebelum satu berkas pun dibaca."""
    _layout(tmp_path, monkeypatch)

    result = RUNNER.invoke(app, ["run", "--dry-run", "--set-model", "writer"])

    assert result.exit_code == 1
    assert "peran=model" in result.output


def test_unknown_role_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Peran yang tidak ada di profil ditolak, dan yang dirujuk disebutkan."""
    _layout(tmp_path, monkeypatch)

    result = RUNNER.invoke(
        app, ["run", "--dry-run", "--rps", str(RPS_PATH), "--set-model", "penyair=gemma3:4b"]
    )

    assert result.exit_code == 1
    assert "penyair" in result.output


def test_unknown_chapter_is_refused_with_the_available_numbers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--chapter 99`` menunjuk bab yang tidak ada — dan menyebut yang ada."""
    layout = _layout(tmp_path, monkeypatch)
    _seed_plan(layout)

    result = RUNNER.invoke(app, ["run", "--dry-run", "--chapter", "99"])

    assert result.exit_code == 1
    assert "Bab 99" in result.output


def test_plan_without_rps_says_what_to_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``plan`` menuntut ``--rps``; pesannya menyebut flag-nya, bukan berkas lain."""
    _layout(tmp_path, monkeypatch)

    result = RUNNER.invoke(app, ["plan"])

    assert result.exit_code == 1
    assert "--rps" in result.output


def test_status_before_any_plan_is_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``status`` pada state kosong menjawab pertanyaan, bukan gagal.

    Bedanya disengaja: ``status`` adalah perintah *membaca keadaan*. State kosong
    adalah keadaan yang sah, dan menjawabnya dengan exit 1 akan membuat skrip
    yang memeriksa status gagal pada buku yang belum dimulai.
    """
    _layout(tmp_path, monkeypatch)

    result = RUNNER.invoke(app, ["status"])

    assert result.exit_code == 0, result.output
    assert "Belum ada rencana buku" in result.output


def test_export_without_a_plan_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Belum ada yang bisa digabung, dan pesannya menunjuk sebabnya."""
    _layout(tmp_path, monkeypatch)

    result = RUNNER.invoke(app, ["export"])

    assert result.exit_code == 1
    assert "Belum ada rencana buku" in result.output


def test_export_joins_approved_chapters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bab yang disetujui dirender dari record, bukan dibaca dari output/chapters/."""
    layout = _layout(tmp_path, monkeypatch)
    _seed_approved_chapter(layout)

    result = RUNNER.invoke(app, ["export"])

    assert result.exit_code == 0, result.output
    book = (layout.output / "book.md").read_text(encoding="utf-8")
    # Berkas gabungan diawali judul buku, lalu babnya — jadi yang diperiksa
    # adalah kehadiran bab itu, bukan bahwa ia berada di baris pertama.
    assert "# Bab 1." in book
    assert "## Latihan" in book
    # Tidak ada satu pun berkas Markdown per bab: isinya tetap tergabung, karena
    # yang dibaca ``export`` adalah ``state/chapterNN.json`` (§28), bukan berkas
    # yang kebetulan ada di direktori keluaran.
    assert list((layout.output / "chapters").glob("*.md")) == []


# ---------------------------------------------------------------------------
# Pembantu berkas
# ---------------------------------------------------------------------------
def _sandbox_markdown(layout: Layout) -> str:
    """Isi Markdown bab 1 yang dihasilkan dry-run, dari dalam sandbox."""
    return (layout.sandbox_output / "chapters" / "chapter01.md").read_text(encoding="utf-8")


def _files_under(base: Path, *, exclude: str | None = None) -> set[str]:
    """Berkas di bawah ``base``, sebagai jalur relatif; ``exclude`` melewati satu subdir."""
    if not base.is_dir():
        return set()
    return {
        path.relative_to(base).as_posix()
        for path in base.rglob("*")
        if path.is_file() and (exclude is None or exclude not in path.relative_to(base).parts)
    }


def _seed_approved_chapter(layout: Layout) -> None:
    """Tulis satu bab yang sudah disetujui langsung ke state, tanpa LLM.

    Dipakai tes ``export``, yang hanya **membaca** state. Menyemainya lewat
    ``run --dry-run`` tidak lagi mungkin — hasil dry-run sengaja dikurung di
    sandbox — dan memang tidak pantas: ``export`` membaca keadaan buku yang
    sungguhan, jadi yang diuji adalah keadaan buku yang sungguhan.
    """
    store = JsonStateStore(layout.state, clock=FixedClock("2026-10-07T12:00:00+00:00"))
    spec = BookSpec(
        title="Buku Uji",
        chapters=(
            ChapterSpec(
                number=1,
                title="Bab Uji",
                objectives=("memahami sesuatu",),
                sections=("Pengantar",),
            ),
        ),
    )
    store.save_book(
        BookState(request=BookRequest(title="Buku Uji", target_chapters=1), spec=spec)
    )
    store.save_chapter(
        ChapterRecord(
            number=1,
            status=ChapterStatus.APPROVED,
            spec=spec.chapters[0],
            draft=ChapterDraft(
                title="Bab Uji",
                learning_objectives=("memahami sesuatu",),
                sections=(Section(heading="Pengantar", body="Isi pengantar."),),
                exercises=("Latihan pertama.",),
            ),
        )
    )


def _seed_waiting_chapter(layout: Layout) -> None:
    """Tulis satu bab yang menunggu keputusan manusia langsung ke state (§44).

    Bukan lewat ``run``: mengaktifkan gate §44 berarti menyunting
    ``pipeline.gates`` untuk tes ini saja, dan yang sedang diuji di sini adalah
    **tampilan** keadaan itu — bukan cara membentuknya. Vonis yang menunggu
    itulah yang membedakannya dari bab yang sekadar terputus di status yang sama.
    """
    store = JsonStateStore(layout.state, clock=FixedClock("2026-10-07T12:00:00+00:00"))
    spec = BookSpec(
        title="Buku Uji",
        chapters=(
            ChapterSpec(
                number=1,
                title="Bab Uji",
                objectives=("memahami sesuatu",),
                sections=("Pengantar",),
            ),
        ),
    )
    store.save_book(
        BookState(request=BookRequest(title="Buku Uji", target_chapters=1), spec=spec)
    )
    store.save_chapter(
        ChapterRecord(
            number=1,
            status=ChapterStatus.LATEX_COMPILED,
            spec=spec.chapters[0],
            draft=ChapterDraft(title="Bab Uji"),
            reviews=(
                ReviewResult(gate="human_approval", approved=False, blocked=True),
            ),
        )
    )


def _repository_runtime_files() -> set[str]:
    """Berkas di ``state/`` dan ``output/`` milik repo, bila ada.

    ``output/`` di-track git (§39), jadi ini bukan sekadar kehati-hatian
    kebersihan: berkas yang muncul di sini berarti satu tes sudah menulis ke
    deliverable.
    """
    found: set[str] = set()
    for name in ("state", "output"):
        found |= {
            f"{name}/{relative}" for relative in _files_under(PROJECT_ROOT / name)
        }
    return found


# ---------------------------------------------------------------------------
# compare (§33)
# ---------------------------------------------------------------------------
def test_compare_runs_the_chapter_once_per_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Satu bab dikerjakan sekali untuk setiap model, masing-masing di direktorinya (§33).

    Yang dibuktikan di sini bukan mutu modelnya — ``--dry-run`` menyintesis
    jawabannya sehingga semua model menghasilkan bab yang sama — melainkan
    **pemisahannya**: dua direktori hasil, dua catatan per peran, dan setiap
    catatan menamai model yang benar-benar menjawab.
    """
    layout = _layout(tmp_path, monkeypatch)
    _seed_plan(layout)

    result = RUNNER.invoke(
        app,
        ["compare", "--dry-run", "--chapter", "1", "--models", "gemma3:4b,gemma4:31b-cloud"],
    )

    assert result.exit_code == 0, result.output

    roots = sorted(path.name for path in (layout.sandbox_output / "compare").iterdir())
    assert len(roots) == 2, roots
    assert (layout.sandbox / "state" / "compare").is_dir()

    logs = sorted((layout.sandbox_output / "compare").glob("*/logs/writer.jsonl"))
    assert len(logs) == 2, logs

    # Yang tercatat harus model yang diminta, bukan model profilnya: itulah yang
    # membuat angka di tabelnya dapat dipercaya.
    named = {
        json.loads(line)["model"]
        for path in logs
        for line in path.read_text(encoding="utf-8").splitlines()
    }
    assert named == {"gemma3:4b", "gemma4:31b-cloud"}

    records = sorted((layout.sandbox / "state" / "compare").glob("*/chapter01.json"))
    assert len(records) == 2, records

    assert not (PROJECT_ROOT / "state" / "compare").exists(
    ), "hasil compare tidak boleh mendarat di state/ repo"


def test_compare_refuses_an_unknown_role_and_lists_the_known_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Peran yang tidak ada di profil aktif harus disebut beserta yang tersedia."""
    layout = _layout(tmp_path, monkeypatch)
    _seed_plan(layout)

    result = RUNNER.invoke(
        app,
        ["compare", "--dry-run", "--role", "penyair", "--models", "gemma3:4b"],
    )

    assert result.exit_code == 1
    assert "penyair" in result.output
    assert "writer" in result.output


def test_compare_without_models_says_how_to_pass_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Daftar model kosong bukan perbandingan; pesannya memberi contoh yang dapat disalin."""
    layout = _layout(tmp_path, monkeypatch)
    _seed_plan(layout)

    result = RUNNER.invoke(app, ["compare", "--dry-run", "--models", ""])

    assert result.exit_code == 1
    assert "--models" in result.output


def test_compare_refuses_a_chapter_the_plan_does_not_have(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bab yang tidak ada di BookSpec ditolak dengan menyebut bab yang ada."""
    layout = _layout(tmp_path, monkeypatch)
    _seed_plan(layout)

    result = RUNNER.invoke(
        app, ["compare", "--dry-run", "--chapter", "99", "--models", "gemma3:4b"]
    )

    assert result.exit_code == 1
    assert "Bab 99" in result.output


# ---------------------------------------------------------------------------
# approve / reject (§44)
# ---------------------------------------------------------------------------
def test_approve_on_a_chapter_that_is_already_approved_is_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Perintah yang dijalankan dua kali karena ragu bukan kesalahan pemakaian.

    Ini keadaan yang paling sering terjadi dalam praktik — gate §44 biasanya
    **mati**, jadi babnya sudah ``APPROVED`` sebelum dosen sempat mengetik
    ``approve``. Menjawabnya dengan exit 1 akan membuat skrip yang menyetujui
    bab gagal pada buku yang justru sudah benar.
    """
    layout = _layout(tmp_path, monkeypatch)
    _seed_approved_chapter(layout)

    result = RUNNER.invoke(app, ["approve", "1"])

    assert result.exit_code == 0, result.output
    assert "sudah disetujui sebelumnya" in result.output


def test_approve_refuses_a_chapter_that_is_not_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bab yang belum dikerjakan tidak punya apa pun untuk disahkan.

    §44 ada untuk mencegah penerbitan yang belum diperiksa; menyetujui bab yang
    belum ditulis akan mengesahkan kekosongan. Pesannya harus menunjuk perintah
    yang benar-benar dibutuhkan, bukan sekadar menyatakan statusnya.
    """
    layout = _layout(tmp_path, monkeypatch)
    _seed_plan(layout)

    result = RUNNER.invoke(app, ["approve", "1"])

    assert result.exit_code == 1
    assert "belum dapat disetujui" in result.output
    assert "run" in result.output


def test_approve_names_the_chapters_the_plan_actually_has(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nomor yang salah adalah kesalahan pengetikan, dan pesannya menyebut yang ada."""
    layout = _layout(tmp_path, monkeypatch)
    _seed_plan(layout)

    result = RUNNER.invoke(app, ["approve", "99"])

    assert result.exit_code == 1
    assert "Bab 99" in result.output


def test_reject_hands_an_approved_chapter_back_to_the_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Jalur yang paling berguna justru saat gate §44 mati.

    Tanpa gate §44, bab yang sudah ``APPROVED`` tidak lagi dilewati gate mana
    pun — tidak ada jalur otomatis yang dapat mengembalikannya ke penulis. Yang
    diperiksa di sini adalah hasil akhirnya: statusnya benar-benar kembali, dan
    alasan yang diketik dosen benar-benar tersimpan (bukan hanya tercetak).
    """
    layout = _layout(tmp_path, monkeypatch)
    _seed_approved_chapter(layout)

    result = RUNNER.invoke(app, ["reject", "1", "--reason", "Contohnya tidak relevan."])

    # Bab yang kembali ke penulis bukan bab yang gagal: exit 0, karena tidak ada
    # yang perlu diperbaiki dari jalannya program.
    assert result.exit_code == 0, result.output
    assert "dikembalikan ke penulis" in result.output

    store = JsonStateStore(layout.state, clock=FixedClock("2026-10-07T12:00:00+00:00"))
    record = store.load_chapter(1)
    assert record is not None
    assert record.status is ChapterStatus.REVISION
    assert record.revision == 1
    last = record.last_review()
    assert last is not None
    assert last.feedback == ("Contohnya tidak relevan.",)
    assert record.markdown_path is None


def test_reject_without_a_reason_still_records_something(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Vonis tanpa catatan terbaca sebagai penolakan yang tidak dapat ditindaklanjuti."""
    layout = _layout(tmp_path, monkeypatch)
    _seed_approved_chapter(layout)

    result = RUNNER.invoke(app, ["reject", "1"])

    assert result.exit_code == 0, result.output
    store = JsonStateStore(layout.state, clock=FixedClock("2026-10-07T12:00:00+00:00"))
    record = store.load_chapter(1)
    assert record is not None
    last = record.last_review()
    assert last is not None
    assert last.feedback == ("Ditolak tanpa alasan tertulis.",)


def test_status_shows_a_waiting_chapter_as_waiting_not_as_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bab yang menunggu keputusan manusia bukan bab yang ditolak (§44).

    Bedanya bukan hiasan: ``tolak`` pada tabel status akan mengirim pembacanya
    mencari kesalahan yang tidak ada. Yang benar adalah kolom ``gate terakhir``
    berbunyi "menunggu", dan itulah satu-satunya tempat keadaan ini terlihat
    tanpa membuka berkas JSON.
    """
    layout = _layout(tmp_path, monkeypatch)
    _seed_waiting_chapter(layout)

    result = RUNNER.invoke(app, ["status"])

    assert result.exit_code == 0, result.output
    assert "menunggu" in result.output
    assert "tolak" not in result.output
