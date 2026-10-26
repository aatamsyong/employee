"""Memory manager: short-term, long-term, task-state, artifacts, pending reviews.

- Short-term memory is per-workflow and cleared when the workflow ends.
- Long-term memory is persisted to ``memory/long_term_memory.json`` with
  atomic writes and a configurable size cap to prevent unbounded growth.
- Pending reviews track human-override decisions that have not yet been
  acted on.
"""

import json
import os
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional

_FILE_LOCK = threading.Lock()

_MAX_LONG_TERM_ENTRIES = 500


class MemoryManager:
    def __init__(self, memory_dir: str) -> None:
        self.memory_dir = memory_dir
        os.makedirs(self.memory_dir, exist_ok=True)
        self._short_term: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._task_states: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._artifacts: Dict[str, List[Dict[str, Any]]] = {}
        self._pending_reviews: Dict[str, Dict[str, Any]] = {}
        self._long_term_path = os.path.join(self.memory_dir, "long_term_memory.json")
        self._long_term: Dict[str, Dict[str, Any]] = self._load_long_term()

    # -- short-term --------------------------------------------------------

    def set_short_term(self, workflow_id: str, key: str, value: Any) -> None:
        scope = self._short_term.setdefault(workflow_id, {})
        scope[key] = {"value": value, "timestamp": time.time()}

    def get_short_term(self, workflow_id: str, key: str, default: Any = None) -> Any:
        entry = self._short_term.get(workflow_id, {}).get(key)
        return default if entry is None else entry["value"]

    def clear_short_term(self, workflow_id: str) -> None:
        self._short_term.pop(workflow_id, None)

    def get_all_short_term(self, workflow_id: str) -> Dict[str, Any]:
        return {k: v["value"] for k, v in self._short_term.get(workflow_id, {}).items()}

    # -- long-term ---------------------------------------------------------

    def _load_long_term(self) -> Dict[str, Dict[str, Any]]:
        if not os.path.exists(self._long_term_path):
            return {}
        try:
            with _FILE_LOCK, open(self._long_term_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_long_term(self) -> None:
        os.makedirs(self.memory_dir, exist_ok=True)
        dir_name = os.path.dirname(self._long_term_path)
        with _FILE_LOCK:
            fd, tmp_path = tempfile.mkstemp(prefix=".lt_", suffix=".json", dir=dir_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self._long_term, f, ensure_ascii=False, indent=2, default=str)
                os.replace(tmp_path, self._long_term_path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

    def _prune_long_term(self) -> int:
        if len(self._long_term) <= _MAX_LONG_TERM_ENTRIES:
            return 0
        sorted_keys = sorted(self._long_term.items(), key=lambda kv: kv[1].get("timestamp", 0))
        to_remove = len(sorted_keys) - _MAX_LONG_TERM_ENTRIES
        for key, _ in sorted_keys[:to_remove]:
            del self._long_term[key]
        return to_remove

    def store_long_term(self, key: str, value: Any, category: str = "general") -> None:
        previous = self._long_term.get(key)
        self._long_term[key] = {
            "value": value,
            "category": category,
            "timestamp": time.time(),
            "previous": previous,
        }
        self._prune_long_term()
        self._save_long_term()

    def retrieve_long_term(self, key: str, default: Any = None) -> Any:
        entry = self._long_term.get(key)
        return default if entry is None else entry["value"]

    def search_long_term(self, query: str) -> Dict[str, Dict[str, Any]]:
        q = query.lower()
        return {k: v for k, v in self._long_term.items()
                if q in k.lower() or q in str(v["value"]).lower()}

    def get_all_long_term(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._long_term)

    # -- task state --------------------------------------------------------

    def update_task_state(self, workflow_id: str, step_name: str,
                          status: str, artifact: Any = None) -> None:
        scope = self._task_states.setdefault(workflow_id, {})
        scope[step_name] = {"status": status, "artifact": artifact, "updated_at": time.time()}

    def get_task_state(self, workflow_id: str) -> Dict[str, Dict[str, Any]]:
        return dict(self._task_states.get(workflow_id, {}))

    # -- artifacts ---------------------------------------------------------

    def register_artifact(self, workflow_id: str, artifact_type: str,
                          file_path: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        scope = self._artifacts.setdefault(workflow_id, [])
        scope.append({
            "artifact_type": artifact_type,
            "file_path": file_path,
            "metadata": metadata or {},
            "registered_at": time.time(),
        })

    def get_artifacts(self, workflow_id: str) -> List[Dict[str, Any]]:
        return list(self._artifacts.get(workflow_id, []))

    # -- pending reviews ---------------------------------------------------

    def set_pending_review(self, workflow_id: str, review: Dict[str, Any]) -> None:
        self._pending_reviews[workflow_id] = {
            **review,
            "created_at": time.time(),
            "resolved": False,
        }

    def resolve_pending_review(self, workflow_id: str, decision: str, actor: str) -> Optional[Dict[str, Any]]:
        pending = self._pending_reviews.get(workflow_id)
        if not pending:
            return None
        pending["resolved"] = True
        pending["decision"] = decision
        pending["actor"] = actor
        pending["resolved_at"] = time.time()
        return dict(pending)

    def get_pending_review(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        return self._pending_reviews.get(workflow_id)

    # -- contamination / cleanup ------------------------------------------

    def detect_contamination(self, workflow_id: str) -> Optional[str]:
        scope = self._short_term.get(workflow_id, {})
        count = len(scope)
        if count > 100:
            return (f"Short-term memory for workflow '{workflow_id}' has {count} entries "
                    f"(threshold: 100). Possible leak.")
        return None

    def cleanup_stale(self, max_age_seconds: int = 3600) -> Dict[str, int]:
        cutoff = time.time() - max_age_seconds
        removed: Dict[str, int] = {}
        for wf_id in list(self._short_term.keys()):
            scope = self._short_term[wf_id]
            stale = [k for k, v in scope.items() if v.get("timestamp", 0) < cutoff]
            if stale:
                removed[wf_id] = len(stale)
                for k in stale:
                    del scope[k]
            if not scope:
                del self._short_term[wf_id]
        return removed
