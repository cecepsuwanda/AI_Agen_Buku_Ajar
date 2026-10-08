"""State machine §27 — tabel ekshaustif dan **setiap** pasangan ilegal.

Berkas ini menguji aturan yang paling mudah rusak tanpa disadari. Tabel transisi
adalah data: menambah satu baris untuk gate baru adalah perubahan satu baris, dan
satu baris yang salah tidak menghasilkan crash di tempat ia ditulis — ia
menghasilkan bab yang berpindah ke status yang salah tiga langkah kemudian.

Karena itu dua hal diuji di sini, dan keduanya harus lengkap:

1. **Seluruh tabel**, pasangan per pasangan, dengan tujuan yang diharapkan.
2. **Seluruh pasangan yang TIDAK ada di tabel**, dibangkitkan dari hasil kali
   kartesius status × event — bukan dari daftar yang ditulis tangan. Daftar
   tulisan tangan hanya akan memuat pasangan yang sudah terpikirkan penulisnya,
   yaitu justru bukan pasangan yang berbahaya.
"""

from __future__ import annotations

import itertools

import pytest

from domain.enums import ChapterEvent, ChapterStatus
from domain.errors import IllegalTransitionError, InvalidGateError
from domain.transitions import (
    CHAIN,
    can_advance,
    status_after_gate,
    transition,
)

ALL_STATUSES = tuple(ChapterStatus)
ALL_EVENTS = tuple(ChapterEvent)

#: Isi tabel §27, ditulis ulang di sini **dengan sengaja**.
#:
#: Bukan duplikasi yang sia-sia: bila seseorang menambah atau mengubah baris di
#: ``domain/transitions.py``, tes ini harus gagal sampai perubahan itu
#: dinyatakan juga di sini. Tabel transisi adalah kontrak, dan kontrak yang
#: dapat berubah tanpa ada yang menyadarinya bukan kontrak.
EXPECTED: dict[tuple[ChapterStatus, ChapterEvent], ChapterStatus] = {
    (ChapterStatus.PLANNED, ChapterEvent.RESEARCH): ChapterStatus.RESEARCHED,
    (ChapterStatus.RESEARCHED, ChapterEvent.DRAFT): ChapterStatus.DRAFTED,
    (ChapterStatus.REVISION, ChapterEvent.DRAFT): ChapterStatus.DRAFTED,
    (ChapterStatus.DRAFTED, ChapterEvent.REVIEW_FAIL): ChapterStatus.FAILED_REVIEW,
    (ChapterStatus.EXAMPLES_WRITTEN, ChapterEvent.REVIEW_FAIL): ChapterStatus.FAILED_REVIEW,
    (ChapterStatus.EXERCISES_WRITTEN, ChapterEvent.REVIEW_FAIL): ChapterStatus.FAILED_REVIEW,
    (ChapterStatus.FACT_CHECKED, ChapterEvent.REVIEW_FAIL): ChapterStatus.FAILED_REVIEW,
    (ChapterStatus.CITATION_CHECKED, ChapterEvent.REVIEW_FAIL): ChapterStatus.FAILED_REVIEW,
    (ChapterStatus.PEDAGOGY_REVIEWED, ChapterEvent.REVIEW_FAIL): ChapterStatus.FAILED_REVIEW,
    (ChapterStatus.CONSISTENCY_CHECKED, ChapterEvent.REVIEW_FAIL): ChapterStatus.FAILED_REVIEW,
    (ChapterStatus.FAILED_REVIEW, ChapterEvent.REVISE): ChapterStatus.REVISION,
    (ChapterStatus.REVIEWED, ChapterEvent.APPROVE): ChapterStatus.APPROVED,
    (ChapterStatus.LATEX_COMPILED, ChapterEvent.APPROVE): ChapterStatus.APPROVED,
}

#: Rantai §27 **sebelum** dua tahap penulisan §19/§20 disisipkan.
#:
#: Ditulis sebagai literal dengan sengaja: ia adalah bentuk lama yang harus
#: tetap bermakna, karena berkas ``state/chapterNN.json`` yang sudah ada di
#: disk memuat status-status ini dan tidak ada yang akan memigrasikannya.
_LEGACY_CHAIN: tuple[ChapterStatus, ...] = (
    ChapterStatus.PLANNED,
    ChapterStatus.RESEARCHED,
    ChapterStatus.DRAFTED,
    ChapterStatus.FACT_CHECKED,
    ChapterStatus.CITATION_CHECKED,
    ChapterStatus.PEDAGOGY_REVIEWED,
    ChapterStatus.CONSISTENCY_CHECKED,
    ChapterStatus.REVIEWED,
    ChapterStatus.LATEX_GENERATED,
    ChapterStatus.LATEX_COMPILED,
    ChapterStatus.APPROVED,
)


# ---------------------------------------------------------------------------
# 1. Tabelnya sendiri
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("status", "event", "expected"), [
    (status, event, target) for (status, event), target in EXPECTED.items()
])
def test_every_declared_transition_produces_its_target(
    status: ChapterStatus, event: ChapterEvent, expected: ChapterStatus
) -> None:
    """Setiap baris tabel menghasilkan tepat tujuannya."""
    assert transition(status, event) is expected


def test_the_table_covers_exactly_the_declared_pairs_plus_fail() -> None:
    """Tidak ada transisi tersembunyi di luar tabel dan di luar ``FAIL``.

    ``FAIL`` adalah satu-satunya peristiwa yang sengaja hidup di luar tabel: ia
    berlaku dari status mana pun yang belum final, dan tabel statis menyatakan
    "dari mana saja" dengan buruk. Selainnya, apa pun yang berhasil harus ada di
    :data:`EXPECTED` — kalau tidak, ada perilaku yang tidak dinyatakan di sini.
    """
    for status in ALL_STATUSES:
        for event in ALL_EVENTS:
            if event is ChapterEvent.FAIL:
                continue
            declared = (status, event) in EXPECTED
            try:
                transition(status, event)
            except IllegalTransitionError:
                succeeded = False
            else:
                succeeded = True
            assert succeeded == declared, (
                f"({status}, {event}) berhasil={succeeded}, "
                f"dinyatakan={declared} — tabel di sini dan di domain/transitions.py "
                f"sudah tidak sinkron"
            )


# ---------------------------------------------------------------------------
# 2. Setiap pasangan ilegal
# ---------------------------------------------------------------------------
def test_every_undeclared_pair_raises() -> None:
    """Seluruh pasangan di luar tabel dan di luar ``FAIL`` ditolak keras."""
    illegal = [
        (status, event)
        for status, event in itertools.product(ALL_STATUSES, ALL_EVENTS)
        if event is not ChapterEvent.FAIL and (status, event) not in EXPECTED
    ]
    # Jaring pengaman: seluruh pasangan dikurangi kolom ``FAIL`` dan tabel yang
    # dinyatakan. Kalau angkanya bergeser tanpa :data:`EXPECTED` ikut berubah,
    # parameternya berubah dan tesnya kehilangan gigi tanpa terlihat gagal.
    expected_count = len(ALL_STATUSES) * (len(ALL_EVENTS) - 1) - len(EXPECTED)
    assert len(illegal) == expected_count
    assert expected_count > 50, "hasil kali kartesius menyusut drastis"

    for status, event in illegal:
        with pytest.raises(IllegalTransitionError) as excinfo:
            transition(status, event)
        assert excinfo.value.status is status
        assert excinfo.value.event is event


# ---------------------------------------------------------------------------
# 3. FAIL: dari mana saja, kecuali yang sudah diterima
# ---------------------------------------------------------------------------
def test_fail_is_allowed_from_every_non_approved_status() -> None:
    """Kegagalan keras boleh datang dari status mana pun kecuali ``APPROVED``."""
    for status in ALL_STATUSES:
        if status is ChapterStatus.APPROVED:
            continue
        assert transition(status, ChapterEvent.FAIL) is ChapterStatus.FAILED


def test_fail_is_refused_from_an_approved_chapter() -> None:
    """Bab yang sudah disetujui tidak boleh ditandai gagal.

    Sebabnya bukan kesopanan: menandai bab yang telah diterima sebagai ``FAILED``
    akan membuat ``run`` berikutnya mengerjakannya ulang dari nol, menghapus
    pekerjaan yang sudah dinilai layak.
    """
    with pytest.raises(IllegalTransitionError) as excinfo:
        transition(ChapterStatus.APPROVED, ChapterEvent.FAIL)
    assert excinfo.value.status is ChapterStatus.APPROVED


def test_fail_is_idempotent_from_a_failed_chapter() -> None:
    """Menandai bab yang sudah gagal sebagai gagal lagi tetap sah."""
    assert transition(ChapterStatus.FAILED, ChapterEvent.FAIL) is ChapterStatus.FAILED


# ---------------------------------------------------------------------------
# 4. Rantai (§27) dan gerbang
# ---------------------------------------------------------------------------
def test_the_chain_is_the_documented_order() -> None:
    """Rantai §27 lengkap, unik, dan berakhir di ``APPROVED``."""
    assert CHAIN[0] is ChapterStatus.PLANNED
    assert CHAIN[-1] is ChapterStatus.APPROVED
    assert len(set(CHAIN)) == len(CHAIN), "rantai tidak boleh memuat status ganda"
    # Seluruh tahap §27 hadir; yang belum berpenghuni pun tetap ada di rantai.
    for status in (
        ChapterStatus.RESEARCHED,
        ChapterStatus.DRAFTED,
        ChapterStatus.EXAMPLES_WRITTEN,
        ChapterStatus.EXERCISES_WRITTEN,
        ChapterStatus.FACT_CHECKED,
        ChapterStatus.CITATION_CHECKED,
        ChapterStatus.PEDAGOGY_REVIEWED,
        ChapterStatus.CONSISTENCY_CHECKED,
        ChapterStatus.REVIEWED,
        ChapterStatus.LATEX_COMPILED,
    ):
        assert status in CHAIN, status


def test_inserting_new_stages_preserves_the_relative_order_of_every_old_pair() -> None:
    """Penyisipan tahap §19/§20 tidak mengubah arti satu pun pasangan status lama.

    Inilah yang membuat state yang sudah ada tetap sah **tanpa migrasi**.
    ``can_advance`` membandingkan **posisi relatif** dua status di rantai — bukan
    jaraknya — jadi menyisipkan status baru di antaranya aman hanya bila urutan
    relatif seluruh pasangan lama tetap sama. Gate yang dulu boleh melompat
    ``DRAFTED → REVIEWED`` harus tetap boleh; gate yang dulu ditolak
    ``REVIEWED → DRAFTED`` harus tetap ditolak. Bila tidak, gate yang sudah
    terdaftar di ``config.yaml`` seseorang akan diam-diam berhenti dijalankan.
    """
    missing = [status for status in _LEGACY_CHAIN if status not in CHAIN]
    assert not missing, f"status yang pernah tertulis ke state hilang dari rantai: {missing}"

    for source in _LEGACY_CHAIN:
        for target in _LEGACY_CHAIN:
            before = _LEGACY_CHAIN.index(target) > _LEGACY_CHAIN.index(source)
            assert can_advance(source, target) is before, f"{source} -> {target}"


def test_advancing_follows_the_chain() -> None:
    """Maju di sepanjang rantai sah; mundur atau tetap di tempat tidak."""
    for index, source in enumerate(CHAIN):
        for other_index, target in enumerate(CHAIN):
            assert can_advance(source, target) == (other_index > index), (
                f"{source} -> {target}"
            )


def test_statuses_outside_the_chain_can_never_be_advanced_to() -> None:
    """``REVISION``/``FAILED_REVIEW``/``FAILED`` bukan tujuan gate mana pun."""
    for outsider in (ChapterStatus.REVISION, ChapterStatus.FAILED_REVIEW, ChapterStatus.FAILED):
        for source in CHAIN:
            assert can_advance(source, outsider) is False, f"{source} -> {outsider}"
        assert can_advance(outsider, ChapterStatus.APPROVED) is False


def test_a_gate_may_skip_forward_over_uninhabited_stages() -> None:
    """Gate boleh melompat maju — itulah yang membuat MVP tetap sah.

    Peninjau MVP memindahkan ``DRAFTED`` langsung ke ``REVIEWED``, melewati
    seluruh tahap yang belum berpenghuni. Rantai 12-tahap tetap utuh, dan gate
    yang menyusul nanti cukup mengambil posisi yang sudah disediakan.
    """
    produced = status_after_gate(ChapterStatus.DRAFTED, ChapterStatus.REVIEWED, gate="reviewer")
    assert produced is ChapterStatus.REVIEWED


def test_a_gate_that_jumps_backwards_is_refused_at_startup() -> None:
    """Gate yang menghasilkan status mundur adalah kesalahan konfigurasi.

    Ditolak saat aplikasi dirakit, bukan saat state sedang ditulis — itulah
    bedanya kesalahan programmer dan kondisi runtime.
    """
    with pytest.raises(InvalidGateError) as excinfo:
        status_after_gate(ChapterStatus.REVIEWED, ChapterStatus.DRAFTED, gate="mundur")
    assert excinfo.value.gate == "mundur"
    assert excinfo.value.source is ChapterStatus.REVIEWED
    assert excinfo.value.target is ChapterStatus.DRAFTED


def test_a_gate_cannot_produce_a_status_outside_the_chain() -> None:
    """Gate tidak boleh menghasilkan ``FAILED``; kegagalan punya jalurnya sendiri."""
    with pytest.raises(InvalidGateError):
        status_after_gate(ChapterStatus.DRAFTED, ChapterStatus.FAILED, gate="x")
