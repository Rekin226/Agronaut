"""Built-in agricultural capabilities; no tool framework or network dependency.

Production systems select engines; irrigation methods are tasks within soil cultivation.
Listing a future task never makes it executable. All dispatch goes through the registry.
"""

from .registry import capabilities, get_capability, run_capability

__all__ = ["capabilities", "get_capability", "run_capability"]
