import asyncio
import logging
import threading
from typing import Any, Callable, List

logger = logging.getLogger(__name__)


class CancellationToken:
    """A thread-safe token used to cancel pending async calls."""

    def __init__(self) -> None:
        self._cancelled: bool = False
        self._lock = threading.Lock()
        self._callbacks: List[Callable[[], None]] = []

    @staticmethod
    def _invoke(callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception:
            # One broken callback must not prevent the remaining cancellations.
            logger.error("Cancellation callback failed")

    def cancel(self) -> None:
        """Cancel linked calls once; execute callbacks outside the lock."""
        with self._lock:
            if self._cancelled:
                return
            self._cancelled = True
            callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            self._invoke(callback)

    def is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def add_callback(self, callback: Callable[[], None]) -> None:
        """Attach a callback, or invoke immediately if already cancelled."""
        with self._lock:
            if not self._cancelled:
                self._callbacks.append(callback)
                return
        self._invoke(callback)

    def link_future(self, future: asyncio.Future[Any]) -> asyncio.Future[Any]:
        """Link on the future's event loop; cancellation may originate in any thread."""
        loop = future.get_loop()

        def _cancel() -> None:
            try:
                current = asyncio.get_running_loop()
            except RuntimeError:
                current = None
            if current is loop:
                future.cancel()
            elif not loop.is_closed():
                loop.call_soon_threadsafe(future.cancel)

        def _unlink(_: asyncio.Future[Any]) -> None:
            with self._lock:
                try:
                    self._callbacks.remove(_cancel)
                except ValueError:
                    pass

        if not future.done():
            future.add_done_callback(_unlink)
            self.add_callback(_cancel)
        return future
