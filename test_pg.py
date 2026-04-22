import asyncio
from langchain_postgres.vectorstores import PGVector
from langchain_ollama import OllamaEmbeddings
from langchain_core.documents import Document

CONNECTION_URI = "postgresql+psycopg://postgres:postgres@localhost:5432/pepper_memory"
COLLECTION_NAME = "users_memory"

embedder = OllamaEmbeddings(model="nomic-embed-text")
vectorstore = PGVector(
    embeddings=embedder,
    collection_name=COLLECTION_NAME,
    connection=CONNECTION_URI,
    use_jsonb=True,
)

docs = vectorstore.similarity_search_with_score("love story jade", k=3)
for d, s in docs:
    print(f"Text: {d.page_content} | Score: {s}")
