import json
import chromadb
from sentence_transformers import SentenceTransformer

# Load model để tạo embedding
embedder = SentenceTransformer("all-MiniLM-L6-v2")

# Khởi tạo ChromaDB client
client = chromadb.PersistentClient(path="./chroma_books")
collection = client.get_or_create_collection(name="books")

def load_books(json_path="static/data/books.json"):
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)

def index_books(books):
    texts = []
    ids = []
    metadatas = []
    for i, b in enumerate(books):
        text = f"{b['title']} | {', '.join(b.get('keywords', []))}"
        texts.append(text)
        ids.append(str(i))
        metadatas.append({"title": b["title"], "price": b["price"]})
    
    embeddings = embedder.encode(texts).tolist()
    collection.add(documents=texts, embeddings=embeddings, ids=ids, metadatas=metadatas)

def search_books(query, top_k=3):
    q_emb = embedder.encode([query]).tolist()[0]
    results = collection.query(query_embeddings=[q_emb], n_results=top_k)
    return results["metadatas"][0] if results["metadatas"] else []
