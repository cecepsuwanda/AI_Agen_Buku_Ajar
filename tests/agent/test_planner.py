"""Tes Book Planner — tiga skenario wajib, semuanya tanpa jaringan.

Pola tiga skenario ini diulang untuk setiap agent:

1. **Bahagia** — keluaran valid pada percobaan pertama; model dipanggil **sekali**.
2. **Rusak lalu baik** — percobaan perbaikan benar-benar terjadi, dan ia memakai
   ``temperature=0.0`` serta tetap menyertakan skema.
3. **Rusak dua kali** — ``AgentOutputError`` dilempar, dan teks mentahnya
   diserahkan ke penyimpan (§37).

Yang ketiga penting bukan karena kegagalannya, melainkan karena **kegagalan itu
tidak hilang**: teks yang tidak dapat di-parse adalah satu-satunya bukti untuk
memperbaiki prompt, dan menelannya adalah cara kehilangan bukti tersebut.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agents.planner import BookPlanner
from app.prompting import FilePromptLibrary
from domain.book import BookRequest, BookSpec, ChapterSpec
from domain.errors import AgentOutputError
from domain.rps import CoursePlan, WeekPlan
from domain.rules import reconcile_book_spec
from tests.fakes.chat_models import SchemaEchoChatModel, ScriptedChatModel

VALID_SPEC = json.dumps(
    {
        "title": "Algoritma dan Struktur Data",
        "language": "id",
        "style_guide": "Bahasa Indonesia akademik.",
        "chapters": [
            {"number": 1, "title": "Pengantar Algoritma", "objectives": ["Menjelaskan algoritma"]},
            {"number": 2, "title": "Analisis Kompleksitas", "objectives": ["Menghitung Big-O"]},
        ],
    },
    ensure_ascii=False,
)


@pytest.fixture
def request_() -> BookRequest:
    """Permintaan buku sederhana dengan RPS pendek."""
    return BookRequest(
        title="Algoritma dan Struktur Data",
        target_chapters=2,
        rps_text="Minggu 1: Pengantar.\nMinggu 2: Kompleksitas.",
        topics=("algoritma", "kompleksitas"),
    )


def _planner(model: Any, prompts: FilePromptLibrary, **kwargs: Any) -> BookPlanner:
    return BookPlanner(model=model, prompts=prompts, **kwargs)


# ---------------------------------------------------------------------------
# 1. Jalur bahagia
# ---------------------------------------------------------------------------
def test_valid_output_on_first_attempt_calls_model_once(
    prompt_library: FilePromptLibrary, request_: BookRequest
) -> None:
    """Keluaran valid langsung diterima; tidak ada percobaan perbaikan."""
    model = ScriptedChatModel([VALID_SPEC])
    spec, notes = _planner(model, prompt_library).plan(request_)

    assert model.call_count == 1
    assert spec.title == "Algoritma dan Struktur Data"
    assert spec.chapter_numbers() == (1, 2)
    assert notes == ()


def test_schema_is_sent_on_every_call(
    prompt_library: FilePromptLibrary, request_: BookRequest
) -> None:
    """``format=`` selalu dikirim — kontrak keluaran tidak pernah dicabut."""
    model = ScriptedChatModel([VALID_SPEC, VALID_SPEC])
    _planner(model, prompt_library).plan(request_)

    schemas = model.schemas()
    assert all(schema is not None for schema in schemas)
    assert "chapters" in schemas[0]["properties"]


def test_prompt_receives_rps_and_target_chapter_count(
    prompt_library: FilePromptLibrary, request_: BookRequest
) -> None:
    """RPS dan jumlah bab benar-benar sampai ke prompt."""
    model = ScriptedChatModel([VALID_SPEC])
    _planner(model, prompt_library).plan(request_)

    sent = model.requests[0].user
    assert "Minggu 2: Kompleksitas." in sent
    assert "2" in sent


def test_missing_rps_is_stated_explicitly(
    prompt_library: FilePromptLibrary,
) -> None:
    """Tanpa RPS, prompt mengatakannya — bukan menyisipkan string kosong."""
    model = ScriptedChatModel([VALID_SPEC])
    request_ = BookRequest(title="Buku", target_chapters=2)

    _planner(model, prompt_library).plan(request_)

    assert "RPS tidak diberikan" in model.requests[0].user


# ---------------------------------------------------------------------------
# 2. Rusak lalu baik — tangga perbaikan
# ---------------------------------------------------------------------------
def test_broken_output_is_repaired_with_zero_temperature(
    prompt_library: FilePromptLibrary, request_: BookRequest
) -> None:
    """Percobaan perbaikan memakai ``temperature=0.0`` dan mengirim skema.

    Menurunkan temperature itu disengaja: kita ingin model **memperbaiki
    kesalahan yang sudah ditunjuk**, bukan berkreasi ulang. Tanpa penurunan ini,
    percobaan kedua bisa menghasilkan kesalahan baru yang berbeda.
    """
    model = ScriptedChatModel(["ini bukan json sama sekali", VALID_SPEC])

    spec, _ = _planner(model, prompt_library).plan(request_)

    assert model.call_count == 2
    assert model.temperatures() == (None, 0.0)
    assert model.schemas()[1] is not None
    assert spec.chapter_numbers() == (1, 2)


def test_repair_prompt_echoes_the_broken_output_and_the_error(
    prompt_library: FilePromptLibrary, request_: BookRequest
) -> None:
    """Prompt perbaikan memuat keluaran rusak, pesan kesalahan, dan prompt asli.

    Prompt asli harus ikut dikirim ulang: ia memuat kontrak OUTPUT, dan
    mengirim instruksi perbaikan sendirian akan menghapus skema yang justru
    ingin ditegakkan.
    """
    model = ScriptedChatModel(["bukan json", VALID_SPEC])

    _planner(model, prompt_library).plan(request_)

    repair = model.requests[1].user
    assert "bukan json" in repair
    assert "PERBAIKAN KELUARAN" in repair
    assert "Minggu 2: Kompleksitas." in repair, "prompt asli hilang dari percobaan perbaikan"


def test_output_with_markdown_fence_is_recovered_without_repair(
    prompt_library: FilePromptLibrary, request_: BookRequest
) -> None:
    """Pagar markdown diselamatkan lebih dulu, sehingga tidak perlu panggilan kedua.

    Model kecil membungkus JSON dengan ```` ```json ```` hampir sepanjang waktu.
    Memperlakukannya sebagai kegagalan berarti membayar dua kali untuk setiap bab.
    """
    fenced = f"Berikut spesifikasinya:\n\n```json\n{VALID_SPEC}\n```\n"
    model = ScriptedChatModel([fenced])

    spec, _ = _planner(model, prompt_library).plan(request_)

    assert model.call_count == 1
    assert spec.chapter_numbers() == (1, 2)


def test_trailing_commas_are_recovered_without_repair(
    prompt_library: FilePromptLibrary, request_: BookRequest
) -> None:
    """Koma di ujung tidak layak dibayar dengan satu panggilan model tambahan."""
    sloppy = VALID_SPEC[:-1] + ",}"
    model = ScriptedChatModel([sloppy])

    spec, _ = _planner(model, prompt_library).plan(request_)

    assert model.call_count == 1
    assert spec.chapter_numbers() == (1, 2)


# ---------------------------------------------------------------------------
# 3. Rusak dua kali — menyerah dengan menyimpan bukti
# ---------------------------------------------------------------------------
def test_persistent_failure_raises_with_the_raw_text(
    prompt_library: FilePromptLibrary, request_: BookRequest
) -> None:
    """Setelah seluruh percobaan habis: ``AgentOutputError`` membawa teks terakhir.

    Teks mentahnya **wajib** ikut di dalam exception. Agent tidak menyimpannya
    sendiri — ia tidak tahu bab mana yang sedang dikerjakannya — sehingga
    pemanggil yang menangkapnya (``BookDirector``) tidak punya cara lain untuk
    menuliskan bukti ke ``state/parse_fail/`` (§37). Diuji di sini karena inilah
    satu-satunya tempat yang dapat membuktikan teks itu tidak hilang di jalan.
    """
    model = ScriptedChatModel(["rusak pertama", "rusak kedua", "rusak ketiga"])
    planner = _planner(model, prompt_library, max_repair_attempts=2)

    with pytest.raises(AgentOutputError) as excinfo:
        planner.plan(request_)

    assert model.call_count == 3, "dua percobaan perbaikan seharusnya terjadi"
    assert excinfo.value.attempts == 3
    assert excinfo.value.raw == "rusak ketiga"


def test_repair_attempts_can_be_disabled(
    prompt_library: FilePromptLibrary, request_: BookRequest
) -> None:
    """``max_repair_attempts=0`` berarti tepat satu panggilan, tanpa perbaikan."""
    model = ScriptedChatModel(["rusak"])

    with pytest.raises(AgentOutputError):
        _planner(model, prompt_library, max_repair_attempts=0).plan(request_)

    assert model.call_count == 1


def test_schema_violation_is_repaired_not_ignored(
    prompt_library: FilePromptLibrary, request_: BookRequest
) -> None:
    """JSON yang sah tetapi melanggar skema juga memicu perbaikan.

    Model dapat menghasilkan JSON yang sempurna secara sintaksis dan tetap salah
    — misalnya bab tanpa field ``title``. Kedua jenis kegagalan harus diperlakukan
    sama, karena keduanya sama-sama tidak menghasilkan tipe domain yang sah.
    """
    missing_title = json.dumps({"chapters": [{"number": 1, "objectives": []}]})
    model = ScriptedChatModel([missing_title, VALID_SPEC])

    spec, _ = _planner(model, prompt_library).plan(request_)

    assert model.call_count == 2
    assert spec.chapter_numbers() == (1, 2)


# ---------------------------------------------------------------------------
# Model echo skema
# ---------------------------------------------------------------------------
def test_schema_echo_model_satisfies_the_planner_contract(
    prompt_library: FilePromptLibrary, request_: BookRequest
) -> None:
    """``SchemaEchoChatModel`` menghasilkan ``BookSpec`` yang sah tanpa JSON ditulis tangan.

    Inilah yang membuat tes integrasi di langkah berikutnya tidak membusuk:
    begitu ``ChapterSpec`` mendapat field baru, model echo mengikutinya sendiri.
    """
    model = SchemaEchoChatModel()

    spec, notes = _planner(model, prompt_library).plan(request_)

    assert isinstance(spec, BookSpec)
    assert model.call_count == 1
    # Model echo menghasilkan satu bab; jumlahnya tidak sesuai target, dan itu
    # memang harus dilaporkan — bukan diperbaiki diam-diam.
    assert any("diminta" in note for note in notes)


# ---------------------------------------------------------------------------
# reconcile_book_spec (murni)
# ---------------------------------------------------------------------------
def test_reconcile_renumbers_chapters_sequentially() -> None:
    """Nomor yang berlompatan dirapikan menjadi berurutan mulai dari 1."""
    spec = BookSpec(
        title="B",
        chapters=(
            ChapterSpec(number=3, title="C"),
            ChapterSpec(number=7, title="A"),
            ChapterSpec(number=9, title="B"),
        ),
    )

    fixed, notes = reconcile_book_spec(spec, target_chapters=3)

    assert [c.number for c in fixed.chapters] == [1, 2, 3]
    assert [c.title for c in fixed.chapters] == ["C", "A", "B"], "urutan harus dipertahankan"
    assert any("berurutan" in note for note in notes)


def test_reconcile_drops_chapters_without_a_title() -> None:
    """Bab tanpa judul dibuang — ia tidak dapat ditulis maupun dirujuk."""
    spec = BookSpec(
        title="B",
        chapters=(ChapterSpec(number=1, title="  "), ChapterSpec(number=2, title="Baik")),
    )

    fixed, notes = reconcile_book_spec(spec, target_chapters=1)

    assert len(fixed.chapters) == 1
    assert fixed.chapters[0].title == "Baik"
    assert any("dibuang" in note for note in notes)


def test_reconcile_reports_chapter_count_mismatch() -> None:
    """Jumlah bab yang tidak sesuai target dilaporkan sebagai catatan."""
    spec = BookSpec(title="B", chapters=(ChapterSpec(number=1, title="A"),))

    _, notes = reconcile_book_spec(spec, target_chapters=8)

    assert any("1 bab" in note and "8 diminta" in note for note in notes)


def test_reconcile_reports_when_nothing_survives() -> None:
    """Spesifikasi yang seluruh babnya dibuang dilaporkan terang-terangan."""
    _, notes = reconcile_book_spec(BookSpec(title="B", chapters=()), target_chapters=8)

    assert any("tidak ada bab" in note for note in notes)


def test_reconcile_is_pure() -> None:
    """Spesifikasi masukan tidak berubah."""
    spec = BookSpec(title="B", chapters=(ChapterSpec(number=5, title="A"),))
    snapshot = spec.model_dump()

    reconcile_book_spec(spec, target_chapters=1)

    assert spec.model_dump() == snapshot


# ---------------------------------------------------------------------------
# course= — RPS terurai sampai ke aturan pemetaan minggu (§12)
# ---------------------------------------------------------------------------
def _course() -> CoursePlan:
    """RPS enam minggu dengan minggu 3 sebagai minggu ujian."""
    return CoursePlan(
        weeks=tuple(
            WeekPlan(number=n, topic=f"Topik {n}", is_assessment=n == 3) for n in range(1, 7)
        )
    )


def test_the_course_plan_is_forwarded_to_the_week_rule(
    prompt_library: FilePromptLibrary, request_: BookRequest
) -> None:
    """``course=`` benar-benar sampai ke :func:`~domain.rules.align_source_weeks`.

    ``VALID_SPEC`` tidak memuat satu pun ``source_weeks``, jadi bab-babnya tidak
    menunjuk materi apa pun. Bila parameter itu berhenti di ``BookPlanner`` dan
    tidak diteruskan, catatan tentang minggu yang tidak terpakai tidak akan
    pernah muncul — dan itulah satu-satunya tanda bahwa pemetaannya bolong.
    """
    model = ScriptedChatModel([VALID_SPEC])

    _, notes = _planner(model, prompt_library).plan(request_, course=_course())

    assert any("minggu kuliah tidak dipakai bab mana pun" in note for note in notes)


def test_without_a_course_the_weeks_are_not_examined(
    prompt_library: FilePromptLibrary, request_: BookRequest
) -> None:
    """RPS yang tidak terbaca berarti tidak ada yang dapat dipakai memeriksa.

    Pemetaannya dibiarkan apa adanya — bukan ditebak. Perencana yang bekerja tanpa
    RPS terurai tetap harus dapat menyelesaikan rencananya.
    """
    model = ScriptedChatModel([VALID_SPEC])

    _, notes = _planner(model, prompt_library).plan(request_)

    assert notes == ()


def test_a_valid_week_mapping_from_the_model_survives_the_agent(
    prompt_library: FilePromptLibrary, request_: BookRequest
) -> None:
    """Pengelompokan yang sah milik model, dan ia melewati seluruh jalur utuh."""
    spec_json = json.dumps(
        {
            "title": "Algoritma dan Struktur Data",
            "chapters": [
                {"number": 1, "title": "A", "source_weeks": ["Minggu 1-2"]},
                {"number": 2, "title": "B", "source_weeks": ["Minggu 4-6"]},
            ],
        },
        ensure_ascii=False,
    )
    model = ScriptedChatModel([spec_json])

    spec, notes = _planner(model, prompt_library).plan(request_, course=_course())

    assert notes == ()
    assert spec.chapters[0].source_weeks == ("Minggu 1-2",)
    assert spec.chapters[1].source_weeks == ("Minggu 4-6",)
