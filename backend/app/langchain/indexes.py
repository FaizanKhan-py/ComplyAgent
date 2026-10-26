"""LangChain INDEXES: the policy knowledge base as a LangChain retriever.

Documents are uploaded, split into chunks and embedded by knowledge_base.py, and the vectors are
stored in the database (the index). PolicyRetriever searches that index and returns LangChain
Document objects, with the file name, page and similarity score in the metadata, so any chain or
agent tool can use the policies the same way.
"""

from __future__ import annotations

import html
from typing import Any

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever


class PolicyRetriever(BaseRetriever):
    """The k chunks most similar to the query, from documents that finished indexing."""

    knowledge_base: Any  # app.knowledge_base.KnowledgeBase (Any: it is not a Pydantic type)
    k: int = 4

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        return [
            Document(
                page_content=chunk["text"],
                metadata={
                    "filename": chunk["filename"],
                    "page_number": chunk["page_number"],
                    "score": chunk["score"],
                    "document_id": chunk["document_id"],
                    "chunk_index": chunk["chunk_index"],
                },
            )
            for chunk in self.knowledge_base.search(query, self.k)
        ]


def format_documents(documents: list[Document]) -> str:
    """Retrieved documents as numbered, escaped <source> blocks: data the model can cite, which
    cannot close its own tag."""
    if not documents:
        return "No indexed policy passage matches this query."
    blocks = []
    for number, doc in enumerate(documents, start=1):
        page = doc.metadata.get("page_number")
        page_attr = f' page="{page}"' if page is not None else ""
        name = html.escape(str(doc.metadata.get("filename", "")), quote=True)
        blocks.append(
            f'<source id="{number}" document="{name}"{page_attr}>\n'
            f"{html.escape(doc.page_content, quote=False)}\n</source>"
        )
    return "\n".join(blocks)
