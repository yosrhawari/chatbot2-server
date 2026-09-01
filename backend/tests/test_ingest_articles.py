from langchain_core.documents import Document

from ingest import (
    chunk_documents,
    find_article_markers,
    split_article_sections,
)

_FR_TEXT = """#### **CONDITIONS GÉNÉRALES HAYETT 2000**

**ARTICLE 1. PREAMBULE**

Le contrat d'Assurance sur Vie dénommé « HAYETT 2000 » comprend des conditions générales.

**ARTICLE 2. DEFINITIONS**

2.1. Vous : le souscripteur. 2.2. Nous : la Compagnie.

**ARTICLE 3. OBJET DU CONTRAT**

Le plan HAYETT 2000 permet de se constituer un capital.
"""

_AR_TEXT = """الفصل 1 توطئة.

هذا العقد يتضمن شروطا عامة.

الفصل 2 المفاهيم

المكتتب هو الشخص الذي يطلب الاكتتاب.

الفصل 10 الاشتراء السابق لأوانه ) شراء كلي او (جزئي

يمكن للمكتتب أن يطلب الاشتراء السابق لأوانه.
"""


class TestFindArticleMarkers:
    def test_french_markers_with_verbatim_titles(self):
        markers = find_article_markers(_FR_TEXT)
        assert len(markers) == 3
        # Line 0 is the "#### CONDITIONS..." header, line 1 blank, marker at 2.
        assert markers[0] == (2, "ARTICLE 1", "PREAMBULE", "fr")
        # Parentheses and punctuation must be kept VERBATIM.
        assert markers[1][2] == "DEFINITIONS"
        assert markers[2][2] == "OBJET DU CONTRAT"

    def test_arabic_markers_keep_full_line_as_title(self):
        markers = find_article_markers(_AR_TEXT)
        assert len(markers) == 3
        assert markers[0][1] == "الفصل 1"
        assert markers[0][2] == "الفصل 1 توطئة."
        assert markers[0][3] == "ar"
        assert markers[2][1] == "الفصل 10"
        assert markers[2][2] == "الفصل 10 الاشتراء السابق لأوانه ) شراء كلي او (جزئي"

    def test_no_markers(self):
        assert find_article_markers("Pas d'articles ici.\nTexte simple.") == []

    def test_empty(self):
        assert find_article_markers("") == []


class TestSplitArticleSections:
    def test_sections_cover_marker_to_next_marker(self):
        markers = find_article_markers(_FR_TEXT)
        sections = split_article_sections(_FR_TEXT, markers)
        assert len(sections) == 3
        assert sections[0]["article"] == "ARTICLE 1"
        assert "comprend des conditions générales" in sections[0]["text"]
        # Section 3 starts at the ARTICLE 3 marker line.
        assert sections[2]["text"].startswith("**ARTICLE 3. OBJET DU CONTRAT**")

    def test_arabic_sections(self):
        markers = find_article_markers(_AR_TEXT)
        sections = split_article_sections(_AR_TEXT, markers)
        assert len(sections) == 3
        assert sections[2]["article"] == "الفصل 10"
        assert "الاشتراء السابق" in sections[2]["text"]

    def test_no_markers_returns_empty(self):
        assert split_article_sections("texte", []) == []


class TestChunkDocuments:
    def test_french_chunks_carry_article_metadata(self):
        docs = [Document(page_content=_FR_TEXT, metadata={"source": "condition_generale.md"})]
        chunks = chunk_documents(docs)
        assert chunks
        for chunk in chunks:
            assert chunk.metadata["source"] == "condition_generale.md"
            assert chunk.metadata["language"] == "fr"
            assert chunk.metadata["article"].startswith("ARTICLE ")
            assert chunk.metadata["title"]

    def test_arabic_chunks_carry_article_metadata(self):
        docs = [Document(page_content=_AR_TEXT, metadata={"source": "condition_arabe.pdf"})]
        chunks = chunk_documents(docs)
        assert chunks
        for chunk in chunks:
            assert chunk.metadata["language"] == "ar"
            assert chunk.metadata["article"].startswith("الفصل ")
            assert chunk.metadata["title"].startswith("الفصل ")

    def test_generic_document_gets_language_only(self):
        docs = [Document(page_content="Texte libre sans articles.\n" * 60, metadata={"source": "note.txt"})]
        chunks = chunk_documents(docs)
        assert chunks
        for chunk in chunks:
            assert "article" not in chunk.metadata
            assert chunk.metadata["language"] == "fr"

    def test_min_chunk_size_respected(self):
        docs = [Document(page_content="tiny", metadata={"source": "x.txt"})]
        assert chunk_documents(docs) == []

    def test_tiny_sections_skipped(self):
        # Whole content is a single short article section: "**ARTICLE 1. X**"
        # is 16 chars — below MIN_CHUNK_CHARS (20) — so nothing is indexed.
        text = "**ARTICLE 1. X**"
        docs = [Document(page_content=text, metadata={"source": "x.md"})]
        chunks = chunk_documents(docs)
        assert chunks == []  # below MIN_CHUNK_CHARS
