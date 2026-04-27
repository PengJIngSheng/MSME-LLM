import asyncio
from langchain_postgres.vectorstores import PGVector
from langchain_ollama import OllamaEmbeddings
from langchain_core.documents import Document
from config_loader import cfg

CONNECTION_URI = cfg.pgvector_connection_uri
COLLECTION_NAME = cfg.pgvector_collection

embedder = OllamaEmbeddings(model=cfg.ollama_embedding_model, base_url=cfg.ollama_base_url)
vectorstore = PGVector(
    embeddings=embedder,
    collection_name=COLLECTION_NAME,
    connection=CONNECTION_URI,
    use_jsonb=True,
)

docs = vectorstore.similarity_search_with_score("love story jade", k=3)
for d, s in docs:
    print(f"Text: {d.page_content} | Score: {s}")
