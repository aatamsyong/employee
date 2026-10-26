"""Tool agent: safe tool invocation with authorization, validation, retry.

All tool calls are gated by the SafetyGovernor (budget, allowed-tool list,
restricted actions) and recorded in the AuditLogger. Output validators
verify artifacts exist on disk before reporting success.
"""

import os
import time
from typing import Any, Callable, Dict, List, Optional

from .tools import generate_docx, generate_pptx


class ToolAgent:
    _TIMEOUT_SECONDS = 30

    def __init__(self, allowed_tools: Optional[List[str]] = None,
                 logger=None, governor=None, memory=None) -> None:
        self.allowed_tools = allowed_tools
        self.logger = logger
        self.governor = governor
        self.memory = memory
        self._registry: Dict[str, Dict[str, Any]] = {}
        self._kb_engine = None
        self._validators: Dict[str, Callable[[Any], bool]] = {}
        self._register_builtins()

    def _register_builtins(self) -> None:
        self.register_tool(
            name="generate_docx", handler=generate_docx,
            description="Create a Word (.docx) document.",
            input_schema={"title": str, "subtitle": str, "sections": list, "output_path": str},
            validator=self._validate_file_output,
        )
        self.register_tool(
            name="generate_pptx", handler=generate_pptx,
            description="Create a PowerPoint (.pptx) document.",
            input_schema={"title": str, "subtitle": str, "slides_data": list, "output_path": str},
            validator=self._validate_file_output,
        )
        self.register_tool(
            name="search_knowledge_base", handler=self._handle_search_kb,
            description="Search the internal knowledge base.",
            input_schema={"query": str, "category_filter": str, "top_k": int},
            validator=lambda r: isinstance(r, list),
        )

    def register_tool(self, name: str, handler: Callable, description: str,
                      input_schema: Optional[Dict[str, type]] = None,
                      requires_confirmation: bool = False,
                      validator: Optional[Callable[[Any], bool]] = None) -> None:
        self._registry[name] = {
            "handler": handler,
            "description": description,
            "input_schema": input_schema or {},
            "requires_confirmation": requires_confirmation,
        }
        if validator:
            self._validators[name] = validator

    def list_tools(self) -> List[Dict[str, Any]]:
        return [{
            "name": n,
            "description": i["description"],
            "input_schema": i["input_schema"],
            "requires_confirmation": i["requires_confirmation"],
        } for n, i in self._registry.items()]

    def set_kb_engine(self, engine) -> None:
        self._kb_engine = engine

    def _handle_search_kb(self, query: str = "", category_filter: str = None,
                          top_k: int = 5) -> List[Dict[str, Any]]:
        if self._kb_engine is None:
            raise RuntimeError("Knowledge base engine not set.")
        return self._kb_engine.search(query=query, category_filter=category_filter, top_k=top_k)

    @staticmethod
    def _validate_file_output(result: Any) -> bool:
        if not isinstance(result, str):
            return False
        try:
            return os.path.isfile(result) and os.path.getsize(result) > 0
        except OSError:
            return False

    def _check_authorization(self, tool_name: str) -> tuple:
        if tool_name not in self._registry:
            return False, f"Tool '{tool_name}' not registered."
        if self.allowed_tools is not None and tool_name not in self.allowed_tools:
            return False, f"Tool '{tool_name}' not in whitelist."
        if self.governor is not None:
            try:
                action_check = self.governor.check_action_allowed(tool_name)
                if not action_check.get("allowed"):
                    return False, action_check.get("reason")
            except Exception as exc:
                return False, f"Governor check failed: {exc}"
        return True, "Authorized."

    def _validate_input(self, tool_name: str, args: Dict[str, Any]) -> tuple:
        schema = self._registry.get(tool_name, {}).get("input_schema", {})
        for param, expected in schema.items():
            if param in args and args[param] is not None and not isinstance(args[param], expected):
                return False, f"Parameter '{param}' expected {expected.__name__}, got {type(args[param]).__name__}."
        return True, "Input valid."

    def _validate_output(self, tool_name: str, result: Any) -> tuple:
        if result is None:
            return False, "Output is None."
        if isinstance(result, str) and result.strip() == "":
            return False, "Output is empty string."
        validator = self._validators.get(tool_name)
        if validator and not validator(result):
            return False, "Output failed validator."
        return True, "Output valid."

    def _record_tool_memory(self, tool_name: str, success: bool, duration_ms: float) -> None:
        if self.memory is None:
            return
        try:
            key = f"tool_{tool_name}"
            existing = self.memory.retrieve_long_term(key, default={"calls": 0, "failures": 0, "total_ms": 0.0})
            if not isinstance(existing, dict):
                existing = {"calls": 0, "failures": 0, "total_ms": 0.0}
            existing["calls"] = int(existing.get("calls", 0)) + 1
            if not success:
                existing["failures"] = int(existing.get("failures", 0)) + 1
            existing["total_ms"] = float(existing.get("total_ms", 0.0)) + duration_ms
            existing["last_call"] = time.time()
            self.memory.store_long_term(key, existing, category="tool_memory")
        except Exception:
            pass

    def run_tool(self, tool_name: str, args: Optional[Dict[str, Any]] = None,
                 workflow_id: Optional[str] = None) -> Dict[str, Any]:
        args = args or {}
        start = time.time()

        if self.governor is not None:
            budget = self.governor.check_tool_call_budget(1)
            if not budget.get("allowed"):
                duration_ms = round((time.time() - start) * 1000, 2)
                self._log(workflow_id, tool_name, False, budget.get("reason"), duration_ms)
                return self._result(False, tool_name, None, budget.get("reason"), duration_ms)

        allowed, reason = self._check_authorization(tool_name)
        if not allowed:
            duration_ms = round((time.time() - start) * 1000, 2)
            self._log(workflow_id, tool_name, False, reason, duration_ms)
            return self._result(False, tool_name, None, reason, duration_ms)

        valid, reason = self._validate_input(tool_name, args)
        if not valid:
            duration_ms = round((time.time() - start) * 1000, 2)
            self._log(workflow_id, tool_name, False, reason, duration_ms)
            return self._result(False, tool_name, None, reason, duration_ms)

        handler = self._registry[tool_name]["handler"]
        try:
            result = handler(**args)
        except Exception as exc:
            duration_ms = round((time.time() - start) * 1000, 2)
            error_msg = f"{type(exc).__name__}: {exc}"
            self._log(workflow_id, tool_name, False, error_msg, duration_ms)
            self._record_tool_memory(tool_name, False, duration_ms)
            return self._result(False, tool_name, None, error_msg, duration_ms)

        elapsed = time.time() - start
        if elapsed > self._TIMEOUT_SECONDS:
            duration_ms = round(elapsed * 1000, 2)
            msg = f"Tool '{tool_name}' exceeded {self._TIMEOUT_SECONDS}s ({elapsed:.1f}s)."
            self._log(workflow_id, tool_name, False, msg, duration_ms)
            self._record_tool_memory(tool_name, False, duration_ms)
            return self._result(False, tool_name, None, msg, duration_ms)

        duration_ms = round(elapsed * 1000, 2)
        output_valid, reason = self._validate_output(tool_name, result)
        if not output_valid:
            self._log(workflow_id, tool_name, False, reason, duration_ms)
            self._record_tool_memory(tool_name, False, duration_ms)
            return self._result(False, tool_name, result, reason, duration_ms)

        self._log(workflow_id, tool_name, True, None, duration_ms)
        self._record_tool_memory(tool_name, True, duration_ms)
        return self._result(True, tool_name, result, None, duration_ms)

    def run_tool_with_retry(self, tool_name: str, args: Optional[Dict[str, Any]] = None,
                            workflow_id: Optional[str] = None, max_retries: int = 2,
                            retry_delay: float = 0.5) -> Dict[str, Any]:
        last: Dict[str, Any] = {}
        for attempt in range(1 + max_retries):
            last = self.run_tool(tool_name, args, workflow_id)
            if last.get("success"):
                return last
            if attempt < max_retries:
                time.sleep(retry_delay)
        return last

    @staticmethod
    def _result(success: bool, tool_name: str, result: Any,
                error: Optional[str], duration_ms: float) -> Dict[str, Any]:
        return {"success": success, "tool_name": tool_name, "result": result,
                "error": error, "duration_ms": duration_ms}

    def _log(self, workflow_id: Optional[str], tool_name: str,
             success: bool, error: Optional[str], duration_ms: float) -> None:
        if self.logger is None:
            return
        status = "SUCCESS" if success else "FAILED"
        self.logger.log_event(
            workflow_id=workflow_id or "NO_WORKFLOW",
            agent_name="ToolAgent",
            action=f"run_tool:{tool_name}",
            status=status,
            details=error or f"Tool '{tool_name}' succeeded.",
            duration_ms=duration_ms,
        )
