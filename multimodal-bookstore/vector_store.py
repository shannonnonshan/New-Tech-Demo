import chromadb
from sentence_transformers import SentenceTransformer
from bson.objectid import ObjectId

# Load model để tạo embedding
embedder = SentenceTransformer("all-MiniLM-L6-v2")

# Khởi tạo ChromaDB client
client = chromadb.PersistentClient(path="./chroma_books")
collection = client.get_or_create_collection(name="books")

def load_books_from_mongo(mongo):
    """Lấy toàn bộ sách từ MongoDB"""
    return list(mongo.db.books.find())

def index_books(books):
    """Index nhiều sách vào ChromaDB"""
    texts = []
    ids = []
    metadatas = []

    for b in books:
        text = f"{b['title']} | {b.get('author', '')}"
        texts.append(text)
        ids.append(str(b["_id"]))  # dùng _id của MongoDB làm ID
        metadatas.append({
            "title": b["title"],
            "price": b.get("price", 0),
            "author": b.get("author", "")
        })

    embeddings = embedder.encode(texts).tolist()
    collection.add(
        documents=texts,
        embeddings=embeddings,
        ids=ids,
        metadatas=metadatas
    )

def search_books(query, top_k=3):
    """Tìm kiếm sách theo ngữ nghĩa bằng ChromaDB"""
    q_emb = embedder.encode([query]).tolist()[0]
    results = collection.query(query_embeddings=[q_emb], n_results=top_k)
    return results["metadatas"][0] if results["metadatas"] else []
