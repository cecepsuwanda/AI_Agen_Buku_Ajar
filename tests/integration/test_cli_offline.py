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
from domain.chapter import ChapterDraft, ChapterRecord, Section
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
    for command in ("doctor", "plan", "run", "write-chapter", "status", "export"):
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
