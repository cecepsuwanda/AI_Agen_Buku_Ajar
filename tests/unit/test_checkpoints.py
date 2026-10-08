"""Tes penyimpanan state — atomik, jujur, dan tidak pernah menghapus apa pun.

Tiga invarian yang diuji di sini, dan ketiganya punya alasan yang sangat konkret:

1. **Penulisan atomik.** Berkas ``.tmp`` ditulis lebih dulu, lalu dipindahkan.
   Tanpa ini, Ctrl+C di saat yang salah meninggalkan ``chapter02.json`` yang
   setengah jadi — dan state setengah jadi lebih buruk daripada state yang
   hilang, karena ia **terbaca** lalu gagal di tempat yang tidak terduga.
2. **``schema_version`` diperiksa sebelum validasi.** Berkas dari versi lain
   harus gagal dengan pesan "versi tidak dikenal", bukan dengan keluhan field
   yang membingungkan.
3. **State rusak tidak pernah dihapus.** Ia bukti. Satu-satunya cara
   menghilangkannya adalah perintah eksplisit dari pengguna.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.container import FixedClock
from domain.book import BookRequest, BookSpec, ChapterSpec
from domain.chapter import ChapterDraft, ChapterRecord, ReviewResult
from domain.enums import ChapterStatus
from domain.errors import StateCorruptError, StateWriteError
from domain.state import BookState
from memory.chapter_state import (
    SUPPORTED_SCHEMA_VERSION,
    decode_book,
    decode_chapter,
    encode_chapter,
    is_approved,
)
from memory.checkpoints import atomic_write_json, atomic_write_text, read_json
from memory.project_state import JsonStateStore

FIXED_TIME = "2026-10-07T12:00:00+00:00"

SPEC = ChapterSpec(number=1, title="Pengantar", objectives=("Menjelaskan algoritma",))


def _record(number: int = 1, **overrides: object) -> ChapterRecord:
    payload: dict[str, object] = {
        "number": number,
        "spec": SPEC,
        "draft": ChapterDraft(title="Pengantar"),
    }
    payload.update(overrides)
    return ChapterRecord.model_validate(payload)


@pytest.fixture
def store(tmp_path: Path) -> JsonStateStore:
    return JsonStateStore(tmp_path / "state", clock=FixedClock(FIXED_TIME))


@pytest.fixture
def book() -> BookState:
    request = BookRequest(title="Algoritma", target_chapters=1)
    spec = BookSpec(title="Algoritma", chapters=(SPEC,))
    return BookState(request=request, spec=spec)


# ---------------------------------------------------------------------------
# 1. Penulisan atomik
# ---------------------------------------------------------------------------
def test_writing_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    """Berkas ``.tmp`` adalah alat, bukan hasil — ia harus hilang setelah sukses."""
    target = tmp_path / "a.json"

    atomic_write_json(target, {"a": 1})

    assert target.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_writing_creates_missing_parent_directories(tmp_path: Path) -> None:
    """``state/parse_fail/`` belum ada saat kegagalan pertama; ia harus dibuat sendiri."""
    target = tmp_path / "state" / "parse_fail" / "x.txt"

    atomic_write_text(target, "isi")

    assert target.read_text(encoding="utf-8") == "isi"


def test_written_files_use_lf_line_endings(tmp_path: Path) -> None:
    """CRLF di ``output/`` yang di-track git berarti diff raksasa setiap perubahan (§39)."""
    target = tmp_path / "a.json"

    atomic_write_json(target, {"teks": "baris satu\nbaris dua"})

    assert b"\r\n" not in target.read_bytes()


def test_non_ascii_text_is_written_readable_not_escaped(tmp_path: Path) -> None:
    """``ensure_ascii=False``: berkas state dibaca manusia, bukan hanya oleh Pydantic."""
    target = tmp_path / "a.json"

    atomic_write_json(target, {"judul": "Analisis Kompleksitas — lanjutan"})

    assert "Analisis Kompleksitas — lanjutan" in target.read_text(encoding="utf-8")


def test_an_existing_file_is_replaced_not_appended(tmp_path: Path) -> None:
    """Kontrak ``os.replace``: pembaca melihat berkas lama atau baru, tidak campurannya."""
    target = tmp_path / "a.json"
    atomic_write_json(target, {"versi": 1})

    atomic_write_json(target, {"versi": 2})

    assert read_json(target) == {"versi": 2}


def test_an_unwritable_target_raises_state_write_error(tmp_path: Path) -> None:
    """Direktori yang menempati nama berkas: kegagalan harus berupa error domain."""
    blocked = tmp_path / "terhalang"
    blocked.mkdir()

    with pytest.raises(StateWriteError):
        atomic_write_text(blocked, "isi")


# ---------------------------------------------------------------------------
# 2. Pembacaan yang menolak menebak
# ---------------------------------------------------------------------------
def test_malformed_json_is_reported_with_its_line_number(tmp_path: Path) -> None:
    """Pesan "baris 3" jauh lebih berguna daripada "JSON tidak sah"."""
    broken = tmp_path / "rusak.json"
    broken.write_text('{\n  "a": 1,\n  oops\n}', encoding="utf-8")

    with pytest.raises(StateCorruptError) as excinfo:
        read_json(broken)

    assert "baris 3" in str(excinfo.value)


def test_a_missing_file_is_reported_as_corrupt_not_as_absent(tmp_path: Path) -> None:
    """``read_json`` tidak pernah "mengembalikan None" — pemanggilnya yang memutuskan."""
    with pytest.raises(StateCorruptError):
        read_json(tmp_path / "tidak-ada.json")


def test_a_foreign_schema_version_is_named_as_such() -> None:
    """Berkas dari build lain harus gagal dengan pesan versi, bukan keluhan field."""
    payload = {"schema_version": 99, "number": 1}

    with pytest.raises(StateCorruptError) as excinfo:
        decode_chapter(payload, source="chapter01.json")

    assert "schema_version 99" in str(excinfo.value)


def test_a_missing_schema_version_is_treated_as_the_current_one() -> None:
    """Berkas lama tanpa ``schema_version`` tetap terbaca — menambah field tidak menaikkan versi."""
    payload = {"number": 1, "spec": SPEC.model_dump(mode="json")}

    record = decode_chapter(payload, source="chapter01.json")

    assert record.schema_version == SUPPORTED_SCHEMA_VERSION


def test_a_non_object_payload_is_refused() -> None:
    with pytest.raises(StateCorruptError) as excinfo:
        decode_book([1, 2, 3], source="book.json")

    assert "bukan objek JSON" in str(excinfo.value)


def test_a_field_violation_is_reported_as_corrupt_not_as_a_crash() -> None:
    """``ValidationError`` bocor sebagai traceback berarti crash, bukan pesan."""
    with pytest.raises(StateCorruptError):
        decode_chapter({"number": 0}, source="chapter01.json")


# ---------------------------------------------------------------------------
# 3. Putaran penuh: simpan → muat
# ---------------------------------------------------------------------------
def test_a_chapter_round_trips_through_disk(store: JsonStateStore) -> None:
    """Field apa pun yang tidak selamat melewati disk adalah field yang hilang saat resume."""
    original = _record(
        status="DRAFTED",
        revision=2,
        research=None,
        markdown_path="output/chapters/chapter01.md",
    )

    store.save_chapter(original)
    loaded = store.load_chapter(1)

    assert loaded is not None
    assert loaded.status is original.status
    assert loaded.revision == 2
    assert loaded.spec == original.spec
    assert loaded.draft == original.draft


def test_a_book_round_trips_through_disk(store: JsonStateStore, book: BookState) -> None:
    store.save_book(book)

    loaded = store.load_book()

    assert loaded is not None
    assert loaded.request.title == "Algoritma"
    assert loaded.spec is not None
    assert loaded.spec.chapter_numbers() == (1,)


def test_loading_an_untouched_chapter_returns_none(store: JsonStateStore) -> None:
    """``None`` = belum dikerjakan. Berkas rusak = error. Keduanya tidak boleh tertukar."""
    assert store.load_chapter(7) is None
    assert store.load_book() is None


def test_saving_stamps_the_time_from_the_injected_clock(store: JsonStateStore) -> None:
    """Jam disuntik supaya berkas hasil tes dapat dibandingkan persis."""
    store.save_chapter(_record())

    assert store.load_chapter(1).updated_at == FIXED_TIME  # type: ignore[union-attr]


def test_the_stamp_is_written_to_disk_not_only_to_the_returned_copy(
    store: JsonStateStore,
) -> None:
    """Yang dibaca alat lain adalah berkasnya, jadi stempelnya harus ada di sana."""
    store.save_chapter(_record())

    raw = json.loads(store.chapter_path(1).read_text(encoding="utf-8"))

    assert raw["updated_at"] == FIXED_TIME


def test_chapter_files_are_zero_padded_so_shell_listing_sorts_correctly(
    store: JsonStateStore,
) -> None:
    assert store.chapter_path(2).name == "chapter02.json"
    assert store.chapter_path(11).name == "chapter11.json"


def test_encoding_is_json_ready_for_other_tools(store: JsonStateStore) -> None:
    """Status ditulis sebagai nilainya, bukan sebagai repr Python."""
    payload = encode_chapter(_record(status="DRAFTED"))

    assert payload["status"] == "DRAFTED"


# ---------------------------------------------------------------------------
# 4. Resume (§28)
# ---------------------------------------------------------------------------
def test_an_approved_chapter_counts_as_completed(store: JsonStateStore) -> None:
    store.save_chapter(_record(status="APPROVED"))

    assert store.is_completed(1) is True


def test_a_failed_chapter_does_not_count_as_completed(store: JsonStateStore) -> None:
    """Bab yang berhenti karena rusak justru yang paling perlu dikerjakan ulang.

    Inilah bedanya dengan ``is_terminal``: status terminal berarti "tidak akan
    diproses lagi", sedangkan "selesai" berarti "tidak perlu diproses lagi".
    """
    store.save_chapter(_record(status="FAILED"))

    assert store.is_completed(1) is False


def test_a_chapter_that_was_never_written_is_not_completed(store: JsonStateStore) -> None:
    assert store.is_completed(3) is False


def test_completed_numbers_selects_only_the_approved_ones(store: JsonStateStore) -> None:
    store.save_chapter(_record(1, status="APPROVED"))
    store.save_chapter(_record(2, status="FAILED_REVIEW"))
    store.save_chapter(_record(3, status="APPROVED"))

    assert store.completed_numbers((1, 2, 3, 4)) == frozenset({1, 3})


def test_corruption_is_not_silently_skipped_during_resume(store: JsonStateStore) -> None:
    """Resume yang diam-diam melewati bab karena berkasnya rusak = pekerjaan yang hilang."""
    store.save_chapter(_record(1, status="APPROVED"))
    store.chapter_path(2).write_text("{ rusak", encoding="utf-8")

    with pytest.raises(StateCorruptError):
        store.completed_numbers((1, 2))


def test_a_corrupt_file_is_left_on_disk(store: JsonStateStore) -> None:
    """State rusak adalah bukti. Tidak ada yang menghapusnya secara otomatis."""
    broken = store.chapter_path(1)
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text("{ rusak", encoding="utf-8")

    with pytest.raises(StateCorruptError):
        store.load_chapter(1)

    assert broken.exists()
    assert broken.read_text(encoding="utf-8") == "{ rusak"


def test_load_chapters_skips_the_ones_that_do_not_exist_yet(store: JsonStateStore) -> None:
    store.save_chapter(_record(1))
    store.save_chapter(_record(3))

    loaded = store.load_chapters((1, 2, 3))

    assert [record.number for record in loaded] == [1, 3]


# ---------------------------------------------------------------------------
# 5. Bukti kegagalan parse (§37)
# ---------------------------------------------------------------------------
def test_a_raw_failure_is_saved_with_its_chapter_and_attempt(store: JsonStateStore) -> None:
    """Nama berkasnya harus menjawab "bab berapa, percobaan ke berapa" tanpa membukanya."""
    path = store.save_raw_failure(2, 3, "ini bukan json")

    assert Path(path).name == "chapter02.attempt3.txt"
    assert Path(path).parent == store.parse_fail_dir


def test_the_saved_failure_says_what_it_is(store: JsonStateStore) -> None:
    """Berkas yang ditemukan orang lain enam bulan lagi harus menjelaskan dirinya sendiri."""
    path = Path(store.save_raw_failure(2, 1, "ini bukan json"))

    text = path.read_text(encoding="utf-8")

    assert "Bab 2, percobaan 1" in text
    assert "Jangan dihapus" in text
    assert text.endswith("ini bukan json")


def test_the_raw_text_is_preserved_verbatim(store: JsonStateStore) -> None:
    """Bukti yang dirapikan bukan lagi bukti — termasuk bila isinya bukan JSON."""
    payload = '```json\n{"a": 1,}\n```'
    path = Path(store.save_raw_failure(1, 2, payload))

    assert payload in path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 6. Penyisipan status baru tidak memaksa migrasi (§19, §20)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("status", ["DRAFTED", "APPROVED"])
def test_a_state_file_written_before_the_new_stages_still_decodes(status: str) -> None:
    """Berkas ``state/chapterNN.json`` dari build lama tetap sah tanpa disentuh.

    ``ChapterStatus`` adalah ``StrEnum``, dan kedua status baru disisipkan
    **sesudah** ``DRAFTED`` — sehingga nilai yang sudah tertulis di disk tetap
    ter-decode. Bila suatu hari penyisipan itu menuntut migrasi, tes ini yang
    akan menolaknya lebih dulu.
    """
    legacy = {
        "schema_version": 1,
        "number": 1,
        "status": status,
        "spec": SPEC.model_dump(mode="json"),
        "draft": {"title": "Pengantar"},
    }

    record = decode_chapter(legacy, source="chapter01.json")

    assert record.status is ChapterStatus(status)
    assert record.number == 1


def test_a_record_without_the_new_field_is_still_valid() -> None:
    """``enriched_draft`` punya bawaan ``None`` — berkas lama tidak memuatnya."""
    review = ReviewResult.model_validate({"gate": "reviewer", "approved": True, "score": 9})

    assert review.enriched_draft is None


def test_an_enriched_draft_is_not_written_into_every_review() -> None:
    """Draf itu sudah disimpan sekali di ``record.draft``; menyimpannya lagi menggandakan berkas.

    ``state/chapterNN.json`` yang dua kali lebih besar hanya untuk menduplikasi
    isi yang sama adalah biaya yang dibayar setiap kali bab dibaca dan ditulis.
    Review yang tersimpan mencatat **vonis**, bukan isi draf.
    """
    enriched = ChapterDraft(title="Pengantar", examples=("Contoh dari gate.",))
    review = ReviewResult(
        gate="example_writer", approved=True, score=10, enriched_draft=enriched
    )
    record = _record(status="EXAMPLES_WRITTEN", draft=enriched, reviews=(review,))

    payload = encode_chapter(record)

    assert "enriched_draft" not in json.dumps(payload, ensure_ascii=False)
    assert payload["draft"]["examples"] == ["Contoh dari gate."]


def test_the_enriched_draft_survives_resume_without_being_duplicated(
    store: JsonStateStore,
) -> None:
    """Yang penting bukan field-nya, melainkan **drafnya tetap ada setelah resume**.

    Membuang ``enriched_draft`` dari review hanya aman karena gate penulisan
    menyerahkannya tepat supaya ia menjadi ``record.draft``. Tes ini membuktikan
    keduanya sekaligus: berkasnya tidak menggandakan isi, dan draf yang diperkaya
    itu benar-benar kembali saat bab dimuat lagi.
    """
    enriched = ChapterDraft(title="Pengantar", examples=("Contoh dari gate.",))
    review = ReviewResult(
        gate="example_writer", approved=True, score=10, enriched_draft=enriched
    )
    store.save_chapter(_record(status="EXAMPLES_WRITTEN", draft=enriched, reviews=(review,)))

    loaded = store.load_chapter(1)

    assert loaded is not None
    assert loaded.draft == enriched
    assert loaded.reviews[0].enriched_draft is None
    assert "enriched_draft" not in store.chapter_path(1).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# is_approved (murni)
# ---------------------------------------------------------------------------
def test_is_approved_distinguishes_done_from_stopped() -> None:
    assert is_approved(_record(status="APPROVED")) is True
    assert is_approved(_record(status="FAILED")) is False
    assert is_approved(_record(status="FAILED_REVIEW")) is False
    assert is_approved(_record(status="REVISION")) is False
