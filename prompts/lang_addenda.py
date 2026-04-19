"""
prompts/lang_addenda.py — Language-specific review rule addenda.
Injected into the review prompt based on detected languages.
"""

LANG_ADDENDA: dict[str, str] = {
    "python": """
### Python Review Rules
- Check for mutable default arguments (e.g., `def f(x=[])`) — they are shared across calls.
- Flag bare `except:` or `except Exception:` that swallow errors silently.
- Verify `async` functions are properly `await`ed at every call site.
- Flag any `eval()` or `exec()` usage as a security concern.
- Check that `Optional[T]` parameters are checked for `None` before use.
- Ensure f-strings don't contain expressions that could raise at runtime.
- Before flagging a missing decorator (e.g., `@with_db_session`), verify the method actually needs it. If the method delegates to a service that manages its own session, the decorator is unnecessary. Trace the call graph first.
""",
    "haskell": """
### Haskell Review Rules
- Verify proper handling of partial functions (`head`, `tail`, `fromJust`, `!!`).
- Check for correct monad transformer stack usage and layering.
- Flag any uses of `unsafePerformIO` or `unsafeCoerce` — require explicit justification.
- Ensure pattern matches are exhaustive or explicitly handle the wildcard case.
- Check for potential space leaks due to improper lazy evaluation.
""",
    "javascript": """
### JavaScript Review Rules
- Flag `==` usage — prefer strict equality `===`.
- Verify proper error handling in async/await chains (missing try/catch).
- Flag any `var` usage — prefer `const` or `let`.
- Check for potential prototype pollution in object merges.
- Ensure callbacks don't silently swallow errors.
""",
    "typescript": """
### TypeScript Review Rules
- Flag `any` types without explicit justification — prefer unknown or generics.
- Verify strict mode compliance and explicit return types on exported functions.
- Check that type assertions (`as`) don't bypass type safety unsafely.
- Ensure generic constraints are properly bounded.
- Verify null/undefined checks before property access.
""",
}
