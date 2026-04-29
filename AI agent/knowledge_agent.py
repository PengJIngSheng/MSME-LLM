import os
import re
import sys
from typing import Iterable

import psycopg
from langchain_core.documents import Document
from langchain_ollama import OllamaEmbeddings
from langchain_postgres.vectorstores import PGVector

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config_loader import cfg


CONNECTION_URI = cfg.pgvector_connection_uri
COLLECTION_NAME = cfg.knowledge_rag_collection

embedder = OllamaEmbeddings(model=cfg.ollama_embedding_model, base_url=cfg.ollama_base_url)
_vectorstore = None


def get_vectorstore() -> PGVector:
    global _vectorstore
    if _vectorstore is None:
        _vectorstore = PGVector(
            embeddings=embedder,
            collection_name=COLLECTION_NAME,
            connection=CONNECTION_URI,
            use_jsonb=True,
        )
    return _vectorstore


def reset_knowledge_collection() -> None:
    """Delete the dedicated Finetune/RAG collection without touching user memory."""
    global _vectorstore
    _vectorstore = None
    with psycopg.connect(CONNECTION_URI.replace("+psycopg", "")) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM langchain_pg_embedding WHERE collection_id IN "
                "(SELECT uuid FROM langchain_pg_collection WHERE name = %s)",
                (COLLECTION_NAME,),
            )
            cur.execute(
                "DELETE FROM langchain_pg_collection WHERE name = %s",
                (COLLECTION_NAME,),
            )
        conn.commit()


def add_knowledge_documents(docs: Iterable[Document], batch_size: int = 64) -> int:
    """Embed and store documents in batches. Returns the number of stored chunks."""
    vectorstore = get_vectorstore()
    batch = []
    total = 0
    for doc in docs:
        if not doc.page_content or not doc.page_content.strip():
            continue
        batch.append(doc)
        if len(batch) >= batch_size:
            vectorstore.add_documents(batch)
            total += len(batch)
            batch = []
    if batch:
        vectorstore.add_documents(batch)
        total += len(batch)
    return total


def _query_terms(query: str) -> set[str]:
    terms = set(re.findall(r"[A-Za-z0-9]{3,}", query.lower()))
    terms.update(re.findall(r"[\u4e00-\u9fff]{2,}", query))
    return terms


def _lexical_bonus(query_terms: set[str], text: str, metadata: dict) -> float:
    haystack = " ".join(
        str(part or "")
        for part in (
            text,
            metadata.get("source"),
            metadata.get("section"),
            metadata.get("agency"),
            metadata.get("filename"),
            metadata.get("title"),
        )
    ).lower()
    if not query_terms:
        return 0.0
    hits = sum(1 for term in query_terms if term in haystack)
    return min(0.30, hits * 0.055)


def retrieve_knowledge_context(query: str, k: int | None = None) -> str:
    """
    Retrieve relevant Finetune knowledge for the current user query.
    Lower PGVector cosine distance is better; score_threshold drops weak matches.
    """
    if not cfg.knowledge_rag_enabled or not query or not query.strip():
        return ""
    try:
        top_k = k or cfg.knowledge_rag_top_k
        threshold = cfg.knowledge_rag_score_threshold
        max_chars = cfg.knowledge_rag_max_context_chars
        fetch_k = max(top_k * 12, 40)
        docs_and_scores = get_vectorstore().similarity_search_with_score(query, k=fetch_k)
        terms = _query_terms(query)

        ranked = []
        for doc, score in docs_and_scores:
            bonus = _lexical_bonus(terms, doc.page_content, doc.metadata or {})
            ranked.append((score - bonus, score, bonus, doc))
        ranked.sort(key=lambda item: item[0])

        blocks = []
        used = 0
        per_block_cap = max(900, min(1800, max_chars // max(top_k, 1)))
        for _adjusted, score, bonus, doc in ranked:
            if score > threshold and bonus <= 0:
                continue
            source = doc.metadata.get("source") or doc.metadata.get("path") or "Finetune"
            title = doc.metadata.get("title") or doc.metadata.get("section") or ""
            text = doc.page_content.strip()[:per_block_cap].rstrip()
            block = f"Source: {source}"
            if title:
                block += f" | Section: {title}"
            block += f"\n{text}"
            if used + len(block) > max_chars:
                remaining = max_chars - used
                if remaining <= 300:
                    break
                block = block[:remaining].rstrip()
            blocks.append(block)
            used += len(block)
            if used >= max_chars:
                break

        if not blocks:
            return ""
        return (
            "[MSME.AI Knowledge Base - Retrieved from Finetune files]\n"
            "Use this internal knowledge when relevant. If it conflicts with newer web results, "
            "explain the difference and prefer the newer source.\n\n"
            + "\n\n---\n\n".join(blocks)
        )
    except Exception as e:
        print(f"[Knowledge RAG] Retrieval Error: {e}")
        return ""
