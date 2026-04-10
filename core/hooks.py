"""Simple hook system for extensibility.

This demonstrates plugin architecture without over-engineering.
Example usage:
    from core.hooks import register_hook, run_hook
    register_hook("post_ingest", my_callback)
    run_hook("post_ingest", span_data)
"""

from typing import Any, Callable, Dict, List

_hooks: Dict[str, List[Callable]] = {}


def register_hook(hook_name: str, callback: Callable) -> None:
    """Register a callback for a hook."""
    if hook_name not in _hooks:
        _hooks[hook_name] = []
    _hooks[hook_name].append(callback)


def run_hook(hook_name: str, *args, **kwargs) -> None:
    """Run all callbacks registered for a hook."""
    for callback in _hooks.get(hook_name, []):
        try:
            callback(*args, **kwargs)
        except Exception as e:
            print(f"[hook] error in {hook_name}: {e}")
