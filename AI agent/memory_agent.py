import os
import json
import re
from langchain_postgres.vectorstores import PGVector
from langchain_ollama import OllamaEmbeddings
from langchain_core.documents import Document

CONNECTION_URI = "postgresql+psycopg://postgres:postgres@localhost:5432/pepper_memory"
COLLECTION_NAME = "users_memory"

embedder = OllamaEmbeddings(model="nomic-embed-text")

# Initialize the PGVector store (this will automatically create tables if they don't exist)
vectorstore = PGVector(
    embeddings=embedder,
    collection_name=COLLECTION_NAME,
    connection=CONNECTION_URI,
    use_jsonb=True,
)

async def extract_and_store_memory(user_id: str, messages: list, llm_callback):
    """
    Use an LLM to extract long-term memory facts from the conversation,
    and save them into PGVector.
    """
    if not user_id or len(messages) < 1:
        return
        
    last_user_msg = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            last_user_msg = msg.get("content", "")
            break

    # We only run extraction if the user's message is substantial
    if len(last_user_msg.strip()) < 10:
        return

    # Extract Memory via LLM
    prompt = f"""
    You are a Memory Extraction AI. 
    Review the user's latest message and extract any permanent facts, preferences, 
    important decisions, or core entity details that should be remembered for future conversations.
    If there is nothing incredibly important to remember, return an empty array [].
    Otherwise, return a JSON array of concise string facts.
    
    User Message: "{last_user_msg}"
    
    Output ONLY a valid JSON array, e.g. ["User prefers dark mode", "User is a financial analyst", "Target company is Microsoft"].
    Do not output any markdown blocks or explanations.
    """
    
    try:
        raw_resp = await llm_callback([{"role": "system", "content": prompt}])
        # clean JSON
        raw_resp = re.sub(r"<think>.*?</think>", "", raw_resp, flags=re.DOTALL).strip()
        if raw_resp.startswith("```json"):
            raw_resp = raw_resp[7:]
        if raw_resp.startswith("```"):
            raw_resp = raw_resp[3:]
        if raw_resp.endswith("```"):
            raw_resp = raw_resp[:-3]
            
        start = raw_resp.find("[")
        end = raw_resp.rfind("]")
        if start != -1 and end != -1:
            facts = json.loads(raw_resp[start:end+1])
            if isinstance(facts, list) and len(facts) > 0:
                docs = [
                    Document(page_content=f, metadata={"user_id": user_id}) 
                    for f in facts if isinstance(f, str)
                ]
                vectorstore.add_documents(docs)
                print(f"[Memory Agent] Saved {len(docs)} facts for user {user_id}")
    except Exception as e:
        print(f"[Memory Agent] Extractor Error for {user_id}: {e}")

def retrieve_memory_context(user_id: str, query: str, k: int = 3) -> str:
    """
    Fetch top-k relevant memories for the user based on the current query.
    """
    if not user_id or not query.strip():
        return ""
    try:
        docs = vectorstore.similarity_search(query, k=k, filter={"user_id": {"$eq": user_id}})
        if not docs:
            return ""
            
        facts = [d.page_content for d in docs]
        return "[System Long-Term Memory (Relevant User Facts)]:\n" + "\n".join(f"- {f}" for f in facts)
    except Exception as e:
        print(f"[Memory Agent] Retrieval Error for {user_id}: {e}")
        return ""
