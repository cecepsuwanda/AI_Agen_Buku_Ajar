"""Agent penyusun buku.

Modul ini meng-import seluruh modul agent agar dekorator @register_gate
tereksekusi dan GATE_REGISTRY terisi. Import-nya sengaja ada di sini —
sehingga composition root cukup meng-import paketnya, tanpa tahu gate apa
saja yang terdaftar (OCP).

Import dilakukan di akhir berkas (setelah nol definisi) supaya urutannya jelas
dan tidak ada yang mengira ada logika di sini.
"""

from agents.base import StructuredAgent
from agents.book_director import BookDirector, DirectorSettings
from agents.chapter_planner import ChapterPlannerAgent
from agents.citation_checker import CitationChecker, CitationCheckerAgent
from agents.example_writer import ExampleWriterAgent, ExampleWriterGate
from agents.exercise_writer import ExerciseWriterAgent, ExerciseWriterGate
from agents.fact_checker import FactChecker, FactCheckerAgent
from agents.gates import PassThroughGate, build_gates, register_gate, registered_gate_names
from agents.latex_qa import LatexQAGate, LatexRepairAgent
from agents.latex_writer import LatexWriterAgent, LatexWriterGate
from agents.pedagogy_reviewer import PedagogyGate, PedagogyReviewerAgent
from agents.planner import BookPlanner
from agents.researcher import NullResearcher, RagResearcher
from agents.reviewer import ChapterReviewer, ChapterReviewerAgent
from agents.writer import ChapterWriter

__all__ = [
    "BookDirector",
    "BookPlanner",
    "ChapterPlannerAgent",
    "ChapterReviewer",
    "ChapterReviewerAgent",
    "ChapterWriter",
    "CitationChecker",
    "CitationCheckerAgent",
    "DirectorSettings",
    "ExampleWriterAgent",
    "ExampleWriterGate",
    "ExerciseWriterAgent",
    "ExerciseWriterGate",
    "FactChecker",
    "FactCheckerAgent",
    "LatexQAGate",
    "LatexRepairAgent",
    "LatexWriterAgent",
    "LatexWriterGate",
    "NullResearcher",
    "PassThroughGate",
    "PedagogyGate",
    "PedagogyReviewerAgent",
    "RagResearcher",
    "StructuredAgent",
    "build_gates",
    "register_gate",
    "registered_gate_names",
]
