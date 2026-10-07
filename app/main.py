"""Shim §42: ``python -m app.main ...`` setara dengan ``python -m app ...``.

Berkas ini ada semata karena blueprint §42 menuliskan baris invokasinya sebagai
``python -m app.main``. Isinya sengaja hanya satu panggilan: seluruh
pengetahuan tentang argumen tinggal di :mod:`app.cli`, dan tidak ada satu pun
perilaku yang hanya dapat dicapai lewat berkas ini. Itu memang tujuannya —
alias yang punya perilaku sendiri adalah alias yang perlahan menjadi jalur
kedua.
"""

from __future__ import annotations

from app.cli import main

if __name__ == "__main__":
    main()
