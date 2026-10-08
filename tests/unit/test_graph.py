"""Tes graf konsep (§14) dan dugaan penyimpangan istilah (§24).

Dua hal diuji di sini, dan keduanya adalah **aturan yang dapat dipastikan
program** — bukan penilaian. Itulah batas yang ditarik ``domain/graph.py``:
sinonim sungguhan seperti contoh §24 sendiri (*finite automaton* /
*finite-state machine*) hanya dapat diputuskan model, sedangkan yang tinggal di
sini hanyalah apa yang benar-benar dapat dihitung.

Yang paling perlu dibuktikan bukan jalur bahagianya, melainkan dua hal yang
mudah dilupakan:

1. **Siklus bukan kasus tepi.** Graf yang dibangun dari ringkasan bab akan
   memuatnya, dan penelusuran yang tidak membawa himpunan yang sudah dikunjungi
   akan berputar sampai ``max_depth`` habis — atau selamanya.
2. **Relasi yang menunjuk sesuatu yang tidak ada dibuang**, bukan disimpan.
   Graf yang memuat janji tentang konsep yang tidak ada di dalamnya adalah graf
   yang penelusurannya berbohong dengan tenang.
"""

from __future__ import annotations

import pytest

from domain.graph import (
    CHECKS,
    PREREQUISITE_RELATIONS,
    RELATIONS,
    ConceptEdge,
    ConceptGraph,
    ConceptNode,
    build_graph,
    canonical_relation,
    detect_terminology_drift,
    find_duplicate_explanations,
    neighbours,
    prerequisites_of,
)


def node(name: str, *, definition: str = "", chapter: int | None = None) -> ConceptNode:
    return ConceptNode(name=name, definition=definition, chapter=chapter)


def edge(source: str, target: str, relation: str = "uses") -> ConceptEdge:
    return ConceptEdge(source=source, target=target, relation=relation)


# ---------------------------------------------------------------------------
# 1. Kosakata §24 dan §14
# ---------------------------------------------------------------------------
def test_the_consistency_checklist_is_the_nine_items_of_section_24() -> None:
    """Daftarnya sembilan, sesuai §24 — bukan sepuluh.

    Angka itu sendiri yang diuji, bukan ingatan siapa pun: daftar inilah yang
    dikirim ke prompt sebagai pertanyaan dan yang dibandingkan
    :func:`~domain.checking.unexamined` dengan temuan model. Daftar yang salah
    panjang berarti satu hal §24 tidak pernah diperiksa siapa pun.
    """
    assert len(CHECKS) == 9
    assert CHECKS == (
        "Terminology",
        "Notation",
        "Acronyms",
        "Definitions",
        "Variables",
        "Chapter references",
        "Figure numbering",
        "Table numbering",
        "Equation numbering",
    )


def test_the_prerequisite_relations_are_a_subset_of_the_known_ones() -> None:
    """Relasi prasyarat tidak boleh memuat ejaan yang tidak ada di ``RELATIONS``."""
    assert PREREQUISITE_RELATIONS <= set(RELATIONS)


@pytest.mark.parametrize("relation", RELATIONS)
def test_every_known_relation_has_a_canonical_spelling(relation: str) -> None:
    """Ejaan baku setiap relasi adalah relasi itu sendiri, apa pun hurufnya."""
    assert canonical_relation(relation) == relation
    assert canonical_relation(relation.upper()) == relation
    assert canonical_relation(f"  {relation}  ") == relation


def test_an_unknown_relation_has_no_canonical_spelling() -> None:
    """``None`` — bukan relasi terdekat. Graf tidak boleh menerka kosakatanya."""
    assert canonical_relation("depends_on") is None


# ---------------------------------------------------------------------------
# 2. Pembentukan graf
# ---------------------------------------------------------------------------
def test_duplicate_names_are_merged_and_the_first_occurrence_wins() -> None:
    """Konsep yang sama ditemukan berkali-kali lintas bab; graf menyimpannya sekali."""
    graph = build_graph(
        [node("DFA", definition="Deterministic Finite Automaton."), node("dfa")],
        [],
    )

    assert graph.names() == ("DFA",)
    assert graph.nodes[0].definition == "Deterministic Finite Automaton."


def test_a_later_occurrence_fills_in_a_definition_the_first_one_lacked() -> None:
    """Kemunculan kedua menambal yang kosong — tetapi tidak menimpa yang terisi."""
    graph = build_graph([node("DFA"), node("DFA", definition="Mesin keadaan berhingga.")], [])

    assert graph.nodes[0].definition == "Mesin keadaan berhingga."


def test_a_later_occurrence_fills_in_a_chapter_the_first_one_lacked() -> None:
    """Sama untuk nomor bab: ``None`` bukan jawaban yang lebih baik daripada 3."""
    graph = build_graph([node("DFA"), node("DFA", chapter=3)], [])

    assert graph.nodes[0].chapter == 3


def test_an_edge_to_an_unknown_concept_is_dropped() -> None:
    """Relasi yang tidak dapat ditelusuri siapa pun tidak disimpan."""
    graph = build_graph([node("Lexer")], [edge("Lexer", "Regular Expression")])

    assert graph.edges == ()


def test_an_edge_with_an_unknown_relation_is_dropped() -> None:
    """Relasi di luar kosakata tidak akan pernah ikut menjawab pertanyaan apa pun."""
    graph = build_graph([node("Lexer"), node("Regex")], [edge("Lexer", "Regex", "depends_on")])

    assert graph.edges == ()


def test_a_self_loop_is_dropped() -> None:
    """"A memakai A" tidak menyatakan apa pun, dan membuat A tetangga dirinya sendiri."""
    graph = build_graph([node("DFA")], [edge("DFA", "DFA")])

    assert graph.edges == ()


def test_duplicate_edges_are_stored_once() -> None:
    """Dua kalimat yang menyatakan hal yang sama tetap satu hubungan."""
    graph = build_graph(
        [node("Lexer"), node("Regex")],
        [edge("Lexer", "Regex"), edge("Lexer", "Regex")],
    )

    assert len(graph.edges) == 1


def test_relation_spelling_is_canonicalised_so_one_relation_is_not_two() -> None:
    """``USES`` dan ``uses`` adalah relasi yang sama bagi siapa pun yang membacanya."""
    graph = build_graph([node("Lexer"), node("Regex")], [edge("Lexer", "Regex", "USES")])

    assert graph.edges[0].relation == "uses"


def test_edge_endpoints_are_matched_ignoring_case() -> None:
    """``dfa`` di simpul dan ``DFA`` di relasi menunjuk konsep yang sama."""
    graph = build_graph([node("DFA")], [edge("dfa", "DFA")])

    # Relasi ini menunjuk dirinya sendiri setelah nama disamakan — jadi ia dibuang.
    assert graph.edges == ()


def test_node_lookup_ignores_case() -> None:
    graph = build_graph([node("Finite Automaton")], [])

    assert graph.node("finite automaton") is not None
    assert graph.node("finite-state machine") is None


# ---------------------------------------------------------------------------
# 3. Penelusuran
# ---------------------------------------------------------------------------
def test_neighbours_lists_only_the_concepts_this_one_points_at() -> None:
    """Arahnya mengikuti §14: apa yang dipakai, bukan siapa yang menyebutnya."""
    graph = build_graph(
        [node("Lexer"), node("Token"), node("Regex")],
        [edge("Lexer", "Token", "produces"), edge("Lexer", "Regex", "uses")],
    )

    assert neighbours(graph, "Lexer") == ("Token", "Regex")
    assert neighbours(graph, "Token") == ()


def test_a_concept_the_graph_does_not_know_has_no_neighbours() -> None:
    assert neighbours(ConceptGraph(), "APA PUN") == ()


def test_prerequisites_follow_only_the_relations_that_mean_one() -> None:
    """Token bukan prasyarat lexer, dan DFA bukan prasyarat yang mengimplementasikannya."""
    graph = build_graph(
        [node("Lexer"), node("Token"), node("Regex")],
        [edge("Lexer", "Token", "produces"), edge("Lexer", "Regex", "uses")],
    )

    assert prerequisites_of(graph, "Lexer") == ("Regex",)


def test_prerequisites_are_breadth_first_and_each_appears_once() -> None:
    """Konsep yang tercapai lewat dua lintasan tetap satu prasyarat."""
    graph = build_graph(
        [node("A"), node("B"), node("C"), node("D")],
        [
            edge("A", "B"),
            edge("A", "C"),
            edge("B", "D"),
            edge("C", "D"),
        ],
    )

    assert prerequisites_of(graph, "A") == ("B", "C", "D")


def test_a_cycle_does_not_send_the_search_around_forever() -> None:
    """Graf dari ringkasan bab memang bersiklus, dan itu ditangani — bukan oleh ``max_depth``.

    "A memakai B" dan "B dijelaskan lewat A" sama-sama benar sebagai kalimat.
    Tanpa himpunan yang sudah dikunjungi, penelusuran ini hanya akan berhenti
    karena ``max_depth`` habis — dan hasilnya bergantung pada batas itu.
    """
    graph = build_graph(
        [node("A"), node("B")],
        [edge("A", "B"), edge("B", "A")],
    )

    assert prerequisites_of(graph, "A") == ("B",)


def test_max_depth_zero_means_no_prerequisites_are_sought() -> None:
    """Batas nol adalah permintaan yang sah, bukan kegagalan."""
    graph = build_graph([node("A"), node("B")], [edge("A", "B")])

    assert prerequisites_of(graph, "A", max_depth=0) == ()


def test_prerequisites_stop_at_the_requested_depth() -> None:
    """``config.graph.max_depth`` benar-benar membatasi panjang lintasannya."""
    graph = build_graph(
        [node("A"), node("B"), node("C")],
        [edge("A", "B"), edge("B", "C")],
    )

    assert prerequisites_of(graph, "A", max_depth=1) == ("B",)
    assert prerequisites_of(graph, "A", max_depth=2) == ("B", "C")


def test_a_concept_is_never_its_own_prerequisite() -> None:
    """Termasuk pada graf bersiklus, tempat "dirinya sendiri" justru dapat tercapai."""
    graph = build_graph([node("A"), node("B")], [edge("A", "B"), edge("B", "A")])

    assert "A" not in prerequisites_of(graph, "A")
    assert "A" not in prerequisites_of(graph, "A", max_depth=50)


# ---------------------------------------------------------------------------
# 4. Penjelasan kembar (§14: duplicate explanation detection)
# ---------------------------------------------------------------------------
def test_two_concepts_with_the_same_definition_are_reported_as_a_group() -> None:
    graph = build_graph(
        [
            node("DFA", definition="Mesin keadaan berhingga."),
            node("Finite Automaton", definition="Mesin keadaan berhingga."),
        ],
        [],
    )

    assert find_duplicate_explanations(graph) == (("DFA", "Finite Automaton"),)


def test_differing_only_in_spacing_or_case_still_counts_as_the_same_definition() -> None:
    """Yang dibandingkan adalah kalimatnya, bukan penulisannya."""
    graph = build_graph(
        [
            node("DFA", definition="Mesin  keadaan berhingga."),
            node("FA", definition="mesin keadaan berhingga."),
        ],
        [],
    )

    assert find_duplicate_explanations(graph) == (("DFA", "FA"),)


def test_a_similar_beginning_is_not_a_duplicate() -> None:
    """Dua definisi yang mirip pada awalnya adalah dua definisi yang berbeda."""
    graph = build_graph(
        [
            node("DFA", definition="Mesin keadaan berhingga tanpa nondeterminisme."),
            node("NFA", definition="Mesin keadaan berhingga dengan nondeterminisme."),
        ],
        [],
    )

    assert find_duplicate_explanations(graph) == ()


def test_concepts_without_a_definition_are_never_a_duplicate_group() -> None:
    """Dua konsep tanpa definisi belum tentu sama; yang pasti keduanya belum dijelaskan."""
    graph = build_graph([node("A"), node("B")], [])

    assert find_duplicate_explanations(graph) == ()


# ---------------------------------------------------------------------------
# 5. Penyimpangan istilah (§24)
# ---------------------------------------------------------------------------
def test_a_hyphen_variant_of_a_known_term_is_reported_with_its_reason() -> None:
    drift = detect_terminology_drift(("finite state machine",), ("finite-state machine",))

    assert len(drift) == 1
    assert drift[0].term == "finite state machine"
    assert drift[0].known == "finite-state machine"
    assert "tanda hubung" in drift[0].reason


def test_an_irregular_plural_is_caught_by_the_model_not_by_this_function() -> None:
    """*automata* bukan *automaton* + "s", dan batas mekanis ini diakui terang-terangan.

    Bentuk jamak tak beraturan seperti ini justru yang paling sering muncul di
    bahan berbahasa Inggris — dan yang paling tidak dapat ditemukan pencocokan
    awalan. Yang menemukannya adalah model, yang menerima kesembilan hal §24
    sebagai pertanyaan.
    """
    assert detect_terminology_drift(("automata",), ("automaton",)) == ()


def test_a_plural_of_a_hyphenated_term_is_reported() -> None:
    drift = detect_terminology_drift(("finite state machines",), ("finite-state machine",))

    assert len(drift) == 1
    assert "jamak" in drift[0].reason


def test_a_true_synonym_is_not_reported_because_only_the_model_can_decide_it() -> None:
    """Batas yang ditarik dengan sadar: §24 menugaskan keputusan itu kepada agent.

    *finite automaton* dan *finite-state machine* memang sinonim, tetapi tidak
    satu pun perbedaan **mekanis** di antara keduanya menyatakannya. Fungsi ini
    mengembalikan kosong, dan prompt §24 tetap menanyakan kesembilan hal itu.
    """
    assert detect_terminology_drift(("finite automaton",), ("finite-state machine",)) == ()


def test_a_term_already_used_by_the_book_is_not_drift() -> None:
    """Istilah buku yang sah muncul di banyak bab — ia bukan penyimpangan."""
    assert detect_terminology_drift(("DFA",), ("DFA",)) == ()


def test_each_term_produces_at_most_one_suspicion() -> None:
    """Satu istilah berkali-kali akan mengubur dugaan yang lain."""
    drift = detect_terminology_drift(
        ("finite state machine",),
        ("finite-state machine", "finite state machines"),
    )

    assert len(drift) == 1
    assert drift[0].known == "finite-state machine"


def test_terms_are_reported_in_the_order_they_were_asked_about() -> None:
    drift = detect_terminology_drift(
        ("state machine", "finite state machine"),
        ("state-machine", "finite-state machine"),
    )

    assert [item.term for item in drift] == ["state machine", "finite state machine"]


def test_an_empty_book_has_nothing_to_drift_from() -> None:
    assert detect_terminology_drift(("apa pun",), ()) == ()


@pytest.mark.parametrize("term", ["", "   "])
def test_a_blank_term_is_not_a_concept(term: str) -> None:
    assert detect_terminology_drift((term,), ("finite-state machine",)) == ()
