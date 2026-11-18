import re
from typing import Any, Callable, Dict, List, Optional, Tuple


class Route:
    def __init__(self, method: str, pattern: str, handler: Callable[..., Any]):
        self.method = method.upper()
        self.pattern = pattern
        self.regex = re.compile(f"^{pattern}$")
        self.handler = handler

    def match(self, method: str, path: str) -> Optional[Dict[str, Any]]:
        if self.method != method.upper():
            return None
        m = self.regex.match(path)
        if not m:
            return None
        return m.groupdict()


class Router:
    """Declarative table-driven HTTP method/path router."""

    def __init__(self):
        self._routes: List[Route] = []

    def add(self, method: str, pattern: str, handler: Callable[..., Any]) -> None:
        self._routes.append(Route(method, pattern, handler))

    def get(self, pattern: str):
        def decorator(fn: Callable[..., Any]):
            self.add("GET", pattern, fn)
            return fn
        return decorator

    def post(self, pattern: str):
        def decorator(fn: Callable[..., Any]):
            self.add("POST", pattern, fn)
            return fn
        return decorator

    def dispatch(self, handler: Any, method: str, path: str, qs: Dict[str, List[str]], body: Dict[str, Any]) -> bool:
        """
        Attempt to match and execute a registered route handler.
        Returns True if a route matched and executed, False if not found.
        """
        for r in self._routes:
            params = r.match(method, path)
            if params is not None:
                r.handler(handler, qs=qs, body=body, **params)
                return True
        return False
