"""Relasi antar-konsep yang dapat dipastikan dari sumbernya (§11, §14).

Hanya satu relasi yang diambil di sini — ``references`` — dan pembatasan itu
disengaja. §14 memberi contoh relasi seperti *Lexer* ``uses`` *Regular
Expression*, tetapi kalimat semacam itu tidak menandai dirinya sebagai relasi
dengan cara yang dapat dibaca mesin; menebaknya dari kedekatan kata akan mengisi
graf dengan hubungan yang tidak pernah ditulis siapa pun, dan graf yang salah
lebih buruk daripada graf yang kosong — ia terlihat dapat dipercaya.

Yang **dapat** dipastikan adalah rujukan silang, sebab LaTeX menyatakannya secara
eksplisit: ``\\label`` menamai sebuah bagian, ``\\ref`` menunjuk nama itu. §14
menyebut "cross-reference" sebagai gunanya, dan inilah wujudnya.

Relasi ``uses``/``produces``/``consumes``/``implemented_by`` menunggu pengekstrak
yang sesungguhnya — pekerjaan model, bukan pekerjaan regex. Kosakatanya sudah
tersedia di :data:`~domain.graph.RELATIONS` supaya pengekstrak itu punya sasaran.
"""

from __future__ import annotations

import re

from domain.graph import ConceptEdge

from graph.entities import sections_of

#: Nama sebuah bagian (§11).
LABEL_PATTERN = re.compile(r"\\label\{([^}]*)\}")

#: Penunjukan ke nama bagian.
REF_PATTERN = re.compile(r"\\ref\{([^}]*)\}")


def _label_index(text: str) -> dict[str, str]:
    """Peta ``label → judul bagian`` untuk satu berkas (MURNI).

    Label pertama yang menang bila sebuah nama dipakai dua kali. LaTeX sendiri
    akan menolak berkas seperti itu, jadi yang penting di sini hanyalah bahwa
    pilihannya **deterministik** — graf yang berubah setiap kali dibangun ulang
    adalah graf yang tidak dapat dibandingkan dengan dirinya sendiri.
    """
    index: dict[str, str] = {}
    for title, body in sections_of(text):
        for label in LABEL_PATTERN.findall(body):
            index.setdefault(label.strip(), title)
    return index


def references_from_latex(text: str) -> tuple[ConceptEdge, ...]:
    """Rujukan silang antar bagian di dalam satu berkas LaTeX (§11, §14), MURNI.

    Rujukan yang menunjuk label di luar berkas ini **tidak** menjadi relasi: graf
    dibangun dari seluruh berkas sekaligus, tetapi berkas yang belum dibaca tidak
    boleh muncul di dalamnya sebagai janji. Rujukan yang menunjuk bagiannya sendiri
    juga dibuang — bukan karena terlarang, melainkan karena
    :func:`~domain.graph.build_graph` akan membuangnya, dan membuangnya di sini
    membuat hasilnya sama apakah fungsi ini dipakai langsung atau lewat sana.

    Duplikat dibuang: satu bagian yang dirujuk lima kali tetap satu hubungan.
    """
    index = _label_index(text)
    found: dict[tuple[str, str], ConceptEdge] = {}
    for title, body in sections_of(text):
        for ref in REF_PATTERN.findall(body):
            target = index.get(ref.strip())
            if target is None or target == title:
                continue
            edge = ConceptEdge(source=title, target=target, relation="references")
            found.setdefault((title, target), edge)
    return tuple(found.values())


__all__ = ["LABEL_PATTERN", "REF_PATTERN", "references_from_latex"]
