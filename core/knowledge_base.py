import os
import glob
from typing import List, Dict, Any

try:
    import docx
except ImportError:
    docx = None

class KnowledgeBaseEngine:
    def __init__(self, kb_root: str):
        self.kb_root = kb_root
        self.documents: List[Dict[str, Any]] = []
        self.refresh_index()

    def refresh_index(self):
        """Scans the 01_Knowledge_Base directory and indexes markdown, text, and docx files."""
        self.documents = []
        if not os.path.exists(self.kb_root):
            return

        for root, _, files in os.walk(self.kb_root):
            for file in files:
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, self.kb_root)
                ext = os.path.splitext(file)[1].lower()
                
                # Derive category from subfolder
                parts = rel_path.split(os.sep)
                category = parts[0] if len(parts) > 1 else "Root"

                content = ""
                if ext in [".md", ".txt"]:
                    try:
                        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                    except Exception as e:
                        content = f"Error reading text file: {e}"
                elif ext == ".docx" and docx is not None:
                    try:
                        doc = docx.Document(file_path)
                        content = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
                    except Exception as e:
                        content = f"Error reading docx: {e}"
                elif ext == ".pdf":
                    content = f"[Dokumen PDF Tersimpan]: {file}. Berisi profil atau data teknis perusahaan."

                if content:
                    # Parse frontmatter metadata if present
                    metadata = {
                        "is_dummy": False,
                        "source_type": "official",
                        "verification_status": "unverified"
                    }
                    if content.startswith("---"):
                        parts_meta = content.split("---", 2)
                        if len(parts_meta) >= 3:
                            meta_text = parts_meta[1]
                            for line in meta_text.strip().split("\n"):
                                if ":" in line:
                                    k, v = line.split(":", 1)
                                    k = k.strip()
                                    v = v.strip().lower()
                                    if v == "true":
                                        metadata[k] = True
                                    elif v == "false":
                                        metadata[k] = False
                                    else:
                                        metadata[k] = v

                    self.documents.append({
                        "file_name": file,
                        "rel_path": rel_path,
                        "abs_path": file_path,
                        "category": category,
                        "extension": ext,
                        "content": content,
                        "size_bytes": os.path.getsize(file_path) if os.path.exists(file_path) else 0,
                        "metadata": metadata
                    })

    def search(self, query: str, category_filter: str = None, top_k: int = 5) -> List[Dict[str, Any]]:
        """Searches indexed documents by query keyword relevance and category."""
        if not query or not self.documents:
            return self.documents[:top_k]

        terms = [t.lower() for t in query.split() if len(t) > 2]
        scored_docs = []

        for doc in self.documents:
            if category_filter and category_filter != "Semua" and doc["category"] != category_filter:
                continue

            score = 0
            content_lower = doc["content"].lower()
            name_lower = doc["file_name"].lower()

            for term in terms:
                # Name match has high weight
                if term in name_lower:
                    score += 15
                # Frequency in content
                term_count = content_lower.count(term)
                if term_count > 0:
                    score += min(term_count * 2, 20)

            # Boost exact phrases
            if query.lower() in content_lower:
                score += 30

            if score > 0:
                scored_docs.append((score, doc))

        # Sort descending by score
        scored_docs.sort(key=lambda x: x[0], reverse=True)
        results = []
        for score, doc in scored_docs[:top_k]:
            doc_copy = dict(doc)
            doc_copy["relevance_score"] = score
            results.append(doc_copy)

        # Fallback if no term matched: return top documents in requested category or general
        if not results:
            for doc in self.documents:
                if not category_filter or category_filter == "Semua" or doc["category"] == category_filter:
                    results.append(doc)
                    if len(results) >= top_k:
                        break

        return results

    def get_categories(self) -> List[str]:
        cats = sorted(list(set(d["category"] for d in self.documents)))
        return ["Semua"] + cats

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_documents": len(self.documents),
            "categories": len(set(d["category"] for d in self.documents)),
            "extensions": list(set(d["extension"] for d in self.documents)),
        }
