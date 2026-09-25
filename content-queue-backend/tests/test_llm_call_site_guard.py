"""
Static-analysis guard: every LLM call site must have cost-attribution
tracing (braintrust_span with user_id in metadata) and per-user budget
enforcement (user_id passed to the call itself).

Why this exists: the llm-gateway-hardening work added Braintrust user_id
metadata to the 10 call sites known at the time, but nothing prevented a new
call site added later from skipping it — that gap was only caught by a
manual, whole-codebase audit (see docs/plans/sota-gap-action-plan.md item 14).
This test is that audit, automated, so a new unwrapped call site fails CI
instead of silently shipping with no cost attribution AND no budget
enforcement (user_id gates both — see check_budget in llm_client.py).

How it works: walks the AST of every .py file under app/, finds every call
to llm_client.chat/structured_chat/embed (the three methods that accept
user_id and call check_budget when it's set), and checks:
  1. The call is lexically inside a `with braintrust_span(...):` block
  2. That braintrust_span call's `metadata=` kwarg is a dict literal
     containing a "user_id" key
  3. The llm_client call itself passes `user_id=` as a kwarg

app/core/llm_client.py (the implementation) and app/core/embedding_cache.py
(call_embed/get_or_create_query_embedding, which do their own internal
braintrust_span wrapping — see that file's docstrings) are exempt from
criteria 1-2, since they're not call sites, they're the wrapping mechanism
itself. Callers of embedding_cache's functions still must pass user_id.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP_DIR = Path(__file__).parent.parent / "app"

_LLM_CLIENT_METHODS = {"chat", "structured_chat", "embed"}

# Files that implement the wrapping mechanism, not call sites — exempt from
# the braintrust_span-wrapping requirement (criteria 1-2 below), but any
# llm_client.* calls in them are still checked for user_id since that's what
# actually gates the budget check regardless of tracing.
_EXEMPT_FROM_SPAN_WRAPPING = {
    APP_DIR / "core" / "llm_client.py",
    APP_DIR / "core" / "embedding_cache.py",
}


def _iter_app_py_files():
    for path in sorted(APP_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _is_llm_client_call(node: ast.Call) -> str | None:
    """Return the method name if this is a call to llm_client.<method>(...),
    else None."""
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr not in _LLM_CLIENT_METHODS:
        return None
    if isinstance(func.value, ast.Name) and func.value.id == "llm_client":
        return func.attr
    return None


def _call_has_user_id_kwarg(node: ast.Call) -> bool:
    return any(kw.arg == "user_id" for kw in node.keywords)


def _is_braintrust_span_call(node: ast.expr) -> ast.Call | None:
    """If node is a `braintrust_span(...)` call, return it."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Name) and func.id == "braintrust_span":
        return node
    if isinstance(func, ast.Attribute) and func.attr == "braintrust_span":
        return node
    return None


def _span_metadata_has_user_id(span_call: ast.Call) -> bool:
    for kw in span_call.keywords:
        if kw.arg == "metadata" and isinstance(kw.value, ast.Dict):
            for key in kw.value.keys:
                if isinstance(key, ast.Constant) and key.value == "user_id":
                    return True
    return False


class _WithStackVisitor(ast.NodeVisitor):
    """Walks the tree tracking the stack of enclosing `with` blocks so we can
    check, for each llm_client call, whether a braintrust_span-with-user_id
    is currently open."""

    def __init__(self, filename: str):
        self.filename = filename
        self.violations: list[str] = []
        self._span_stack: list[bool] = []  # True = span has user_id in metadata

    def visit_With(self, node: ast.With):
        opened = 0
        for item in node.items:
            span_call = _is_braintrust_span_call(item.context_expr)
            if span_call is not None:
                self._span_stack.append(_span_metadata_has_user_id(span_call))
                opened += 1
        self.generic_visit(node)
        for _ in range(opened):
            self._span_stack.pop()

    def visit_Call(self, node: ast.Call):
        method = _is_llm_client_call(node)
        if method is not None:
            has_user_id_kwarg = _call_has_user_id_kwarg(node)
            span_open_with_user_id = any(self._span_stack)
            span_open_at_all = len(self._span_stack) > 0

            if not has_user_id_kwarg:
                self.violations.append(
                    f"{self.filename}:{node.lineno}: llm_client.{method}(...) "
                    f"does not pass user_id= — bypasses per-user budget "
                    f"enforcement (check_budget in llm_client.py)."
                )
            elif not span_open_at_all:
                self.violations.append(
                    f"{self.filename}:{node.lineno}: llm_client.{method}(...) "
                    f"is not wrapped in a `with braintrust_span(...):` block — "
                    f"no cost-attribution tracing."
                )
            elif not span_open_with_user_id:
                self.violations.append(
                    f"{self.filename}:{node.lineno}: llm_client.{method}(...) "
                    f"is wrapped in braintrust_span(...), but none of the "
                    f"enclosing span(s) have metadata={{'user_id': ...}} — "
                    f"cost attribution won't be grouped by user."
                )
        self.generic_visit(node)


def _find_violations_in_file(path: Path) -> list[str]:
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))
    visitor = _WithStackVisitor(str(path.relative_to(APP_DIR.parent)))
    visitor.visit(tree)
    return visitor.violations


def test_every_llm_client_call_site_has_span_and_user_id():
    all_violations: list[str] = []
    for path in _iter_app_py_files():
        if path in _EXEMPT_FROM_SPAN_WRAPPING:
            # Still check user_id on any llm_client.* calls inside these
            # files (llm_client.py has none; this guards against that
            # changing silently), just skip the span-wrapping requirement.
            source = path.read_text()
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    method = _is_llm_client_call(node)
                    if method is not None and not _call_has_user_id_kwarg(node):
                        all_violations.append(
                            f"{path.relative_to(APP_DIR.parent)}:{node.lineno}: "
                            f"llm_client.{method}(...) does not pass user_id="
                        )
            continue
        all_violations.extend(_find_violations_in_file(path))

    assert not all_violations, (
        "Found LLM call site(s) missing braintrust_span wrapping and/or "
        "user_id — this bypasses per-user budget enforcement and/or cost "
        "attribution. Wrap the call in `with braintrust_span(name, "
        "metadata={'user_id': ...}):` and pass user_id= to the call itself "
        "(see any existing call site in app/tasks/embedding.py for the "
        "pattern):\n\n" + "\n".join(all_violations)
    )


# ---------------------------------------------------------------------------
# Self-tests for the guard logic itself — verify it actually catches the
# violation shapes it claims to, using synthetic source strings rather than
# real app/ files (so these don't drift if app/ code changes).
# ---------------------------------------------------------------------------


def _violations_for_source(source: str) -> list[str]:
    tree = ast.parse(source, filename="synthetic.py")
    visitor = _WithStackVisitor("synthetic.py")
    visitor.visit(tree)
    return visitor.violations


class TestGuardLogicItself:
    def test_correctly_wrapped_call_has_no_violations(self):
        source = """
with braintrust_span("task", metadata={"user_id": user_id}):
    result = llm_client.chat(messages=[], task="x", user_id=user_id)
"""
        assert _violations_for_source(source) == []

    def test_unwrapped_call_is_flagged(self):
        source = """
result = llm_client.chat(messages=[], task="x", user_id=user_id)
"""
        violations = _violations_for_source(source)
        assert len(violations) == 1
        assert "not wrapped" in violations[0]

    def test_missing_user_id_kwarg_is_flagged(self):
        source = """
with braintrust_span("task", metadata={"user_id": user_id}):
    result = llm_client.chat(messages=[], task="x")
"""
        violations = _violations_for_source(source)
        assert len(violations) == 1
        assert "does not pass user_id=" in violations[0]

    def test_span_without_user_id_in_metadata_is_flagged(self):
        source = """
with braintrust_span("task", input={"q": "x"}):
    result = llm_client.chat(messages=[], task="x", user_id=user_id)
"""
        violations = _violations_for_source(source)
        assert len(violations) == 1
        assert "none of the enclosing span(s)" in violations[0]

    def test_span_with_metadata_but_no_user_id_key_is_flagged(self):
        source = """
with braintrust_span("task", metadata={"other_key": "x"}):
    result = llm_client.chat(messages=[], task="x", user_id=user_id)
"""
        violations = _violations_for_source(source)
        assert len(violations) == 1

    def test_nested_span_satisfies_requirement(self):
        """A user_id-bearing span further up the call stack (not the
        innermost with block) still satisfies the check."""
        source = """
with braintrust_span("outer", metadata={"user_id": user_id}):
    with some_other_context_manager():
        result = llm_client.chat(messages=[], task="x", user_id=user_id)
"""
        assert _violations_for_source(source) == []

    def test_embed_and_structured_chat_are_also_checked(self):
        source = """
result = llm_client.embed(text)
result2 = llm_client.structured_chat(messages=[], response_model=X, task="x")
"""
        violations = _violations_for_source(source)
        assert len(violations) == 2

    def test_unrelated_method_calls_are_ignored(self):
        source = """
result = llm_client.some_other_method()
result2 = other_object.chat()
"""
        assert _violations_for_source(source) == []
