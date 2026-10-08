"""Graf konsep antar-bab (§14) dan kosakata yang menjaganya tetap konsisten (§24) — MURNI.

§14 menutup uraiannya dengan satu kalimat yang menentukan bentuk berkas ini:
"Knowledge Graph bersifat **opsional pada MVP**". Karena itu yang dibangun di
sini adalah bagian yang dapat dipertanggungjawabkan — bentuk grafnya, operasi
penelusurannya, dan satu-satunya pemeriksaan §24 yang jawabannya sudah diketahui
program sebelum model dipanggil — bukan seluruh yang dapat dibayangkan.

**Mengapa tanpa ``networkx``.** Graf di sini berisi puluhan konsep, bukan jutaan
simpul, dan seluruh operasi yang §14 sebutkan — prasyarat, tetangga, penjelasan
kembar — adalah penelusuran sederhana. Menuliskannya sendiri membuat setiap
aturannya dapat diuji habis-habisan tanpa satu pun dependensi, dan berkas ini
tetap terbaca sebagai aturan, bukan sebagai pemanggilan pustaka.

**Siklus bukan kasus tepi.** Graf yang dibangun dari ringkasan bab akan
mengandungnya: "A memakai B" dan "B dijelaskan lewat A" sama-sama benar sebagai
kalimat, dan keduanya dapat muncul di dalam satu buku. Setiap penelusuran di sini
karena itu membawa himpunan yang sudah dikunjungi — dan itu diuji langsung,
bukan diserahkan kepada ``max_depth``.

**Batas yang ditarik berkas ini.** Yang tinggal di sini hanyalah pertanyaan yang
jawabannya dapat dipastikan program: apakah dua istilah berbeda hanya pada tanda
hubung, bentuk jamak, atau besar-kecil huruf. Sinonim sungguhan — *finite
automaton* dan *finite-state machine* pada contoh §24 — tidak dapat dipastikan
secara mekanis, dan §24 memang menugaskan keputusan itu kepada agent: "Agent
harus menentukan apakah ketiga istilah memang sinonim atau harus
distandardisasi". Dugaan mekanis di sini karena itu dikirim **ke** model sebagai
pertanyaan, bukan dipakai sebagai jawabannya.

Perhatikan bahwa §24 ternyata memuat **sembilan** hal yang harus diperiksa, bukan
sepuluh seperti yang sempat disebut rencana tahap ini. Yang dihitung adalah
daftarnya sendiri (:data:`CHECKS`), bukan ingatan siapa pun.
"""

from __future__ import annotations

from typing import Sequence

from pydantic import Field

from domain.base import FrozenModel

#: Relasi antar-konsep yang §14 sebutkan, ditambah satu yang §14 minta lewat
#: daftar gunanya ("cross-reference").
#:
#: Ditulis sebagai daftar tertutup, bukan ``str`` bebas: relasi yang ditulis
#: dengan ejaan sendiri oleh setiap pengekstrak akan menghasilkan graf yang
#: penelusurannya tidak dapat diandalkan — dan kesalahan seperti itu tidak
#: menimbulkan galat, hanya prasyarat yang tidak pernah ditemukan.
RELATIONS: tuple[str, ...] = (
    "uses",
    "produces",
    "consumes",
    "implemented_by",
    "references",
)

#: Relasi yang menyatakan "konsep tujuan harus dipahami lebih dulu".
#:
#: Penelusuran prasyarat berjalan dari konsep **menuju** apa yang dipakainya,
#: bukan dari apa yang memakainya. ``produces`` dan ``implemented_by`` sengaja
#: tidak termasuk: token bukan prasyarat lexer, dan DFA bukan prasyarat bagi
#: apa pun yang mengimplementasikannya.
PREREQUISITE_RELATIONS: frozenset[str] = frozenset({"uses", "consumes"})

#: Sembilan hal yang §24 minta diperiksa pada setiap bab baru, urut blueprint.
#:
#: Dipakai sebagai daftar periksa yang dijawab satu per satu oleh prompt, dan
#: sebagai sasaran :func:`~domain.checking.unexamined`. Tanpa daftar tertutup,
#: pemeriksa yang menyebut enam dari sembilan hal tidak dapat dibedakan dari
#: pemeriksa yang memeriksa kesembilannya.
CHECKS: tuple[str, ...] = (
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

#: Batas kedalaman penelusuran prasyarat bawaan.
#:
#: Bukan pengaman terhadap siklus — siklus sudah ditangani himpunan yang
#: dikunjungi. Ia membatasi panjang lintasan pada graf yang tidak bersiklus
#: tetapi sangat dalam, dan ``config.graph.max_depth`` dapat menimpanya dari
#: composition root.
DEFAULT_MAX_DEPTH: int = 8


class ConceptNode(FrozenModel):
    """Satu konsep di dalam graf (§14).

    ``chapter`` boleh kosong: konsep yang berasal dari bahan rujukan belum tentu
    milik satu bab, dan mengarang nomor bab untuknya akan membuat
    :func:`prerequisites_of` menunjuk ke tempat yang salah.
    """

    name: str = Field(min_length=1, description="Nama konsep sebagaimana ditulis di dalam bahan.")
    definition: str = Field(
        default="", description="Definisi terpendek yang ditemukan, apa adanya."
    )
    chapter: int | None = Field(default=None, ge=1, description="Bab tempat konsep ini muncul.")


class ConceptEdge(FrozenModel):
    """Satu hubungan berarah antar dua konsep (§14)."""

    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    relation: str = Field(min_length=1, description=f"Salah satu dari {RELATIONS}.")


class ConceptGraph(FrozenModel):
    """Graf konsep satu buku (§14).

    Disimpan sebagai dua ``tuple``, bukan sebagai peta ketetanggaan: yang ditulis
    ``graph/knowledge_graph.py`` adalah graf ini apa adanya, dan peta ketetanggaan
    adalah **turunan** yang dihitung setiap kali dipakai. Menyimpannya berarti dua
    representasi yang sama harus dijaga tetap sinkron, dan yang kedua selalu yang
    tertinggal.
    """

    nodes: tuple[ConceptNode, ...] = ()
    edges: tuple[ConceptEdge, ...] = ()

    def names(self) -> tuple[str, ...]:
        """Nama seluruh konsep, urut penyimpanan (MURNI)."""
        return tuple(node.name for node in self.nodes)

    def node(self, name: str) -> ConceptNode | None:
        """Konsep bernama ``name``, pencocokan mengabaikan besar-kecil huruf (MURNI)."""
        wanted = _key(name)
        for node in self.nodes:
            if _key(node.name) == wanted:
                return node
        return None


class TerminologyEntry(FrozenModel):
    """Satu istilah beserta definisinya, sebagaimana disetujui gate §24.

    Bentuknya sama dengan isi ``BookState.terminology`` — dan itu disengaja:
    apa yang dikembalikan model, apa yang disimpan gate, dan apa yang dibaca bab
    berikutnya harus tiga hal yang sama persis. Perbedaan sekecil apa pun di
    antaranya hanya akan terlihat sebagai prompt yang aneh, jauh dari sebabnya.
    """

    term: str = Field(min_length=1, description="Istilah, apa adanya dari bab.")
    definition: str = Field(
        default="", description="Satu kalimat: apa arti istilah itu di buku ini."
    )


class TerminologyDrift(FrozenModel):
    """Istilah bab ini yang berbeda secara mekanis dari istilah yang sudah dipakai buku (§24).

    ``reason`` menyebutkan **apa** perbedaannya, bukan sekadar bahwa ada
    perbedaan: model yang menerima "keduanya mirip" tidak punya apa pun untuk
    diputuskan, sedangkan "hanya berbeda bentuk jamak" adalah pertanyaan yang
    jelas — sinonim, atau salah tulis?
    """

    term: str = Field(min_length=1, description="Istilah di bab yang sedang diperiksa.")
    known: str = Field(min_length=1, description="Istilah yang sudah dipakai bab-bab sebelumnya.")
    reason: str = Field(description="Perbedaan mekanis yang membuat keduanya diduga sama.")


# ---------------------------------------------------------------------------
# Pembentukan graf
# ---------------------------------------------------------------------------
def _key(name: str) -> str:
    """Kunci pencocokan nama konsep (MURNI)."""
    return name.strip().casefold()


#: :data:`RELATIONS` yang dapat dicari lewat ejaan apa pun.
_RELATION_BY_KEY: dict[str, str] = {relation.casefold(): relation for relation in RELATIONS}


def canonical_relation(relation: str) -> str | None:
    """Ejaan baku sebuah relasi, atau ``None`` bila tidak dikenal (MURNI).

    Pencocokannya mengabaikan besar-kecil huruf, sebab ``uses`` dan ``Uses`` adalah
    relasi yang sama bagi siapa pun yang membacanya — dan graf yang memuat keduanya
    sebagai dua hal berbeda adalah graf yang penelusurannya harus tahu tentang
    keduanya.
    """
    return _RELATION_BY_KEY.get(relation.strip().casefold())


def build_graph(
    nodes: Sequence[ConceptNode], edges: Sequence[ConceptEdge]
) -> ConceptGraph:
    """Rakit graf dari konsep dan relasi yang ditemukan (MURNI).

    Tiga hal dibersihkan di sini, dan ketiganya adalah hal yang tidak dapat
    dipercayakan kepada pengekstrak mana pun:

    * **Nama kembar disatukan**, kemunculan pertama yang menang — beserta
      definisi dan bab pertamanya. Konsep yang sama pasti ditemukan berkali-kali
      lintas bab, dan graf dengan lima simpul "DFA" adalah graf yang penelusurannya
      bercabang ke tempat yang sama lima kali.
    * **Relasi yang menunjuk konsep tak dikenal dibuang.** Ia tidak dapat
      ditelusuri siapa pun, dan menyimpannya berarti grafnya memuat janji tentang
      sesuatu yang tidak ada di dalamnya.
    * **Relasi kembar dan gelung sendiri dibuang.** "A memakai A" tidak
      menyatakan apa pun, dan menyimpannya membuat :func:`neighbours` menyebut
      konsep itu sendiri sebagai tetangganya.
    * **Relasi di luar :data:`RELATIONS` dibuang, dan ejaannya dibakukan.** Relasi
      yang tidak dikenal satu pun penelusuran tidak akan pernah ikut menjawab
      pertanyaan apa pun; menyimpannya hanya membuat grafnya tampak lebih kaya
      daripada yang sesungguhnya. Ejaan yang berbeda besar-kecil huruf dibakukan
      supaya graf tidak memuat dua hal yang sama.
    """
    kept: dict[str, ConceptNode] = {}
    for node in nodes:
        key = _key(node.name)
        if not key:
            continue
        previous = kept.get(key)
        if previous is None:
            kept[key] = node
            continue
        if not previous.definition.strip() and node.definition.strip():
            kept[key] = previous.model_copy(update={"definition": node.definition})
        if previous.chapter is None and node.chapter is not None:
            current = kept[key]
            kept[key] = current.model_copy(update={"chapter": node.chapter})

    accepted: dict[tuple[str, str, str], ConceptEdge] = {}
    for edge in edges:
        source, target = _key(edge.source), _key(edge.target)
        if not source or not target or source == target:
            continue
        if source not in kept or target not in kept:
            continue
        relation = canonical_relation(edge.relation)
        if relation is None:
            continue
        edge_key = (source, target, _key(relation))
        accepted.setdefault(edge_key, edge.model_copy(update={"relation": relation}))

    return ConceptGraph(nodes=tuple(kept.values()), edges=tuple(accepted.values()))


def neighbours(graph: ConceptGraph, concept: str) -> tuple[str, ...]:
    """Konsep yang **ditunjuk** ``concept``, urut kemunculan relasi (MURNI).

    Arahnya mengikuti §14: yang dicari adalah apa yang dipakai, dihasilkan, atau
    dirujuk sebuah konsep — bukan siapa yang menyebutnya. Untuk yang terakhir
    tidak ada pertanyaan di blueprint ini, dan menambahkannya berarti menambah
    operasi yang tidak dipakai siapa pun.
    """
    wanted = _key(concept)
    found: dict[str, None] = {}
    for edge in graph.edges:
        if _key(edge.source) != wanted:
            continue
        target = graph.node(edge.target)
        if target is not None:
            found.setdefault(target.name, None)
    return tuple(found)


def prerequisites_of(
    graph: ConceptGraph, concept: str, *, max_depth: int = DEFAULT_MAX_DEPTH
) -> tuple[str, ...]:
    """Seluruh prasyarat ``concept``, terdekat lebih dulu, tanpa dirinya (MURNI).

    Penelusurannya melebar per tingkat atas :data:`PREREQUISITE_RELATIONS`. Konsep
    yang dapat dicapai lewat dua lintasan berbeda hanya muncul **sekali**, pada
    lintasan terpendeknya — dan itu memang yang benar bagi pembaca: "DFA memakai
    Notasi" yang disebut dua kali bukan dua prasyarat.

    Graf yang bersiklus tidak membuatnya berputar: simpul yang sudah dikunjungi
    tidak ditelusuri lagi. ``max_depth`` karena itu membatasi **kedalaman**, bukan
    keamanan — dan ``max_depth=0`` berarti tidak ada prasyarat yang dicari,
    bukan berarti pencariannya gagal.

    :returns: nama konsep sebagaimana disimpan graf, tanpa konsep awalnya sendiri.
    """
    start = _key(concept)
    if start not in {_key(name) for name in graph.names()} or max_depth <= 0:
        return ()

    frontier = [start]
    visited: set[str] = {start}
    found: dict[str, None] = {}
    for _ in range(max_depth):
        following: list[str] = []
        for name in frontier:
            for edge in graph.edges:
                if _key(edge.source) != name or _key(edge.relation) not in PREREQUISITE_RELATIONS:
                    continue
                target = graph.node(edge.target)
                if target is None:
                    continue
                key = _key(target.name)
                if key in visited:
                    continue
                visited.add(key)
                found.setdefault(target.name, None)
                following.append(key)
        if not following:
            break
        frontier = following

    return tuple(found)


def find_duplicate_explanations(graph: ConceptGraph) -> tuple[tuple[str, ...], ...]:
    """Kelompok konsep yang definisinya sama persis (§14) (MURNI).

    §14 menyebut "duplicate explanation detection" sebagai salah satu gunanya.
    Yang dibandingkan adalah **seluruh** definisi, bukan penggalannya: dua definisi
    yang mirip pada awal kalimat adalah dua definisi yang berbeda, dan
    melaporkannya hanya akan menghasilkan temuan yang tidak dapat dikerjakan —
    penulisnya tidak tahu bagian mana yang harus dihapus.

    Definisi kosong tidak pernah dihitung kembar. Dua konsep tanpa definisi belum
    tentu sama; yang dapat dipastikan hanyalah bahwa keduanya belum dijelaskan.
    """
    groups: dict[str, list[str]] = {}
    for node in graph.nodes:
        definition = " ".join(node.definition.split()).casefold()
        if not definition:
            continue
        groups.setdefault(definition, []).append(node.name)

    return tuple(tuple(names) for names in groups.values() if len(names) > 1)


# ---------------------------------------------------------------------------
# Penyimpangan istilah (§24)
# ---------------------------------------------------------------------------
def _variants(term: str) -> tuple[str, str]:
    """Dua bentuk normal istilah untuk pencocokan mekanis (MURNI).

    Yang pertama hanya membuang pemisah, yang kedua juga membuang bentuk jamak.
    Keduanya dikembalikan sekaligus supaya alasan sebuah dugaan dapat disebut
    apa adanya, alih-alih ditebak dari selisih karakternya.
    """
    spaced = " ".join(term.replace("-", " ").replace("_", " ").split()).casefold()
    words = (
        word[:-1] if len(word) > 3 and word.endswith("s") else word
        for word in spaced.split()
    )
    return spaced, " ".join(words)


def detect_terminology_drift(
    terms: Sequence[str], known: Sequence[str]
) -> tuple[TerminologyDrift, ...]:
    """Istilah ``terms`` yang tampak sebagai varian istilah ``known`` (§24), MURNI.

    Ini pemeriksaan **mekanis**, dan batasnya ditarik terang-terangan: yang dapat
    dipastikannya hanyalah perbedaan tanda hubung, bentuk jamak sederhana, dan
    besar-kecil huruf. Persis jenis penyimpangan yang paling sering terjadi —
    istilah Inggris yang dipakai ulang di bab berbeda oleh penulis yang berbeda —
    tetapi **bukan** sinonim sungguhan seperti contoh §24 sendiri (*finite
    automaton* / *finite-state machine*), yang hanya dapat diputuskan model.

    Karena itu hasilnya berupa **dugaan**, bukan temuan: yang dikembalikan adalah
    pasangan beserta alasan perbedaannya, dan gate §24 mengirimkannya kepada model
    sebagai pertanyaan yang harus dijawab.

    Setiap istilah menghasilkan paling banyak satu dugaan — pasangan ``known``
    pertama yang cocok. Istilah buku yang sah muncul di banyak bab, dan
    melaporkan satu istilah berkali-kali akan mengubur dugaan yang lain.

    :returns: dugaan urut kemunculan ``terms``; **kosong** berarti tidak ada satu
        pun istilah bab ini yang berbeda secara mekanis dari istilah buku.
    """
    drifts: list[TerminologyDrift] = []
    seen: set[str] = set()

    for term in terms:
        stripped = term.strip()
        key = stripped.casefold()
        if not key or key in seen:
            continue
        seen.add(key)

        spaced, singular = _variants(stripped)
        for other in known:
            known_stripped = other.strip()
            known_key = known_stripped.casefold()
            if not known_key or known_key == key:
                continue
            known_spaced, known_singular = _variants(known_stripped)
            reason = _drift_reason(
                spaced=spaced,
                singular=singular,
                known_spaced=known_spaced,
                known_singular=known_singular,
            )
            if reason is None:
                continue
            drifts.append(
                TerminologyDrift(term=stripped, known=known_stripped, reason=reason)
            )
            break

    return tuple(drifts)


def _drift_reason(
    *,
    spaced: str,
    singular: str,
    known_spaced: str,
    known_singular: str,
) -> str | None:
    """Alasan dua istilah diduga sama, atau ``None`` bila keduanya berbeda (MURNI).

    Urutan pemeriksaannya dari yang paling sempit ke yang paling longgar, sebab
    alasan yang paling sempit itulah yang paling dapat dipercaya pembacanya.
    """
    if spaced == known_spaced:
        return "hanya berbeda tanda hubung atau spasi"
    if singular == known_singular:
        return "hanya berbeda bentuk jamak"
    if singular == known_spaced or spaced == known_singular:
        return "berbeda bentuk jamak dan tanda hubung sekaligus"
    return None


__all__ = [
    "CHECKS",
    "DEFAULT_MAX_DEPTH",
    "PREREQUISITE_RELATIONS",
    "RELATIONS",
    "ConceptEdge",
    "ConceptGraph",
    "ConceptNode",
    "TerminologyDrift",
    "TerminologyEntry",
    "build_graph",
    "canonical_relation",
    "detect_terminology_drift",
    "find_duplicate_explanations",
    "neighbours",
    "prerequisites_of",
]
