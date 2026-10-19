"""Entry point: ``python -m machina.gateway``.

stdin/stdout are the protocol channel. Before anything from ``machina`` is imported
they are moved to private, non-inheritable file descriptors, and fds 0/1 are
re-pointed at the null device / stderr. That guarantees:

- a stray ``print()`` or Rich console write lands in stderr, not the JSON stream;
- child processes (``git``, the terminal tool) never inherit the protocol pipes.
  On Windows a child that inherits a pipe while this process has a blocking read
  pending on it hangs at startup until the read completes.

That fd re-pointing runs only when this module is executed as the entry point
(``python -m machina.gateway``, ``__name__ == "__main__"``). Importing the
module leaves the importer's streams untouched, which lets tests drive the
dispatch loop in-process.
"""

from __future__ import annotations

import os
import sys

if __name__ == "__main__":
    sys.stdout.flush()
    _protocol_in = os.fdopen(os.dup(0), "rb", buffering=0)
    _protocol_out = os.fdopen(os.dup(1), "w", encoding="utf-8", newline="\n")
    _null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(_null, 0)
    os.close(_null)
    os.dup2(2, 1)
    sys.stdin = open(os.devnull, encoding="utf-8")  # lives for the process
    sys.stdout = sys.stderr
else:
    # Imported (tests): keep the protocol channel on the importer's streams.
    _protocol_in = getattr(sys.stdin, "buffer", None)
    _protocol_out = sys.stdout

import asyncio
import json
import logging
import secrets
import threading
from queue import Full, Queue
from typing import Any

logger = logging.getLogger(__name__)

# Bounded hand-off between the event loop and the protocol writer thread.
# A UI that stops draining the pipe must never freeze the loop (control
# requests such as prompt.cancel have to stay dispatchable) and must never
# grow this process's memory without limit. 4096 frames is far above any
# healthy UI's backlog; reaching it means the channel is effectively dead.
_WRITE_QUEUE_MAX = 4096
_write_queue: Queue[str | None] = Queue(maxsize=_WRITE_QUEUE_MAX)
_write_thread: threading.Thread | None = None
_write_failures = 0

# Inbound protocol lines are bounded too: a flooding client applies
# back-pressure to its own reader thread instead of growing the queue
# without limit.
_READ_QUEUE_MAX = 1024

# Control requests preempt the serial request chain (H21). Each exists to
# cancel, resolve, revoke, or stop in-flight work, so queueing one behind a
# slow harness/delegation/discovery request defeats its purpose. The set is
# deliberately small: every other request keeps strict arrival ordering.
_CONTROL_METHODS = frozenset({"prompt.cancel", "approvals.resolve", "mode.revoke", "shutdown"})
# Grace given to requests already read when the loop exits (shutdown/EOF)
# before their handlers are cancelled and joined.
REQUEST_DRAIN_TIMEOUT_SECONDS = 10.0
REQUEST_CANCEL_GRACE_SECONDS = 0.35
WRITER_FLUSH_TIMEOUT_SECONDS = 5.0


def _writer_loop() -> None:
    """Write protocol frames off the event loop, in order.

    ``write``/``flush`` on a pipe the UI stopped draining blocks until the
    pipe buffer frees; running that on the loop thread would make cancel and
    shutdown requests undispatchable. A single thread preserves frame order.
    """
    while True:
        line = _write_queue.get()
        if line is None:
            return
        try:
            _protocol_out.write(line)
            _protocol_out.flush()
        except (OSError, ValueError) as exc:
            global _write_failures
            _write_failures += 1
            # The UI went away or the fd is non-blocking. Keep the loop alive
            # (stdin EOF still drives shutdown) but never lose the evidence.
            if _write_failures == 1 or _write_failures % 100 == 0:
                logger.error("gateway protocol write failed (%d so far): %s", _write_failures, exc)


def _start_writer() -> None:
    global _write_thread
    if _write_thread is not None and _write_thread.is_alive():
        return
    _write_thread = threading.Thread(
        target=_writer_loop, name="machina-gateway-stdout", daemon=True
    )
    _write_thread.start()


def _stop_writer() -> None:
    """Flush queued frames, then stop the writer thread (bounded)."""
    thread = _write_thread
    if thread is None or not thread.is_alive():
        return
    try:
        _write_queue.put(None, timeout=WRITER_FLUSH_TIMEOUT_SECONDS)
    except Full:
        logger.error(
            "gateway protocol writer backlog exceeded; %d frame(s) discarded", _write_queue.qsize()
        )
        return
    thread.join(WRITER_FLUSH_TIMEOUT_SECONDS)
    if thread.is_alive():
        logger.error(
            "gateway protocol writer did not drain within %.0fs", WRITER_FLUSH_TIMEOUT_SECONDS
        )


def _write(frame: dict[str, Any]) -> None:
    """Hand one frame to the writer thread; never blocks the event loop."""
    line = json.dumps(frame, ensure_ascii=False, default=str) + "\n"
    _start_writer()
    try:
        _write_queue.put_nowait(line)
    except Full:
        # Back-pressure is exhausted: the UI is not draining. Dropping the
        # frame keeps the loop responsive so the user can still cancel or
        # shut the gateway down; log it rather than losing it silently.
        logger.error(
            "gateway protocol write queue is full (%d frames); dropping a frame", _WRITE_QUEUE_MAX
        )


def _put_inbound(
    queue: asyncio.Queue[str | None], loop: asyncio.AbstractEventLoop, item: str | None
) -> bool:
    """Block the reader thread while the bounded queue is full; False if closed."""
    try:
        future = asyncio.run_coroutine_threadsafe(queue.put(item), loop)
    except RuntimeError:
        return False
    try:
        future.result()
    except Exception:  # loop closed/cancelled while the reader was parked
        return False
    return True


def _start_stdin_reader(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[str | None]) -> None:
    """Read protocol lines on a thread (portable across Windows pipes and consoles)."""

    def pump() -> None:
        try:
            for raw in iter(_protocol_in.readline, b""):
                line = raw.decode("utf-8", errors="replace").strip()
                if line and not _put_inbound(queue, loop, line):
                    return
        finally:
            _put_inbound(queue, loop, None)

    threading.Thread(target=pump, name="machina-gateway-stdin", daemon=True).start()


def _request_method(line: str) -> str | None:
    """Best-effort method name for dispatch; malformed frames return None."""
    try:
        message = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(message, dict):
        return None
    method = message.get("method")
    return method if isinstance(method, str) else None


async def _dispatch_requests(gateway: Any, queue: asyncio.Queue[str | None]) -> None:
    """Dispatch protocol lines so control requests stay dispatchable (H21).

    Ordinary requests are chained: each starts only after its predecessor
    finishes, preserving the strict arrival-order semantics of the former
    ``await handle_line()`` loop for shared-state mutations. Control requests
    (_CONTROL_METHODS) are awaited immediately instead of behind the chain,
    so a slow harness/delegation/discovery request cannot delay cancellation,
    an approval decision, revocation, or shutdown.

    On exit (shutdown or EOF) every request already read gets up to
    ``REQUEST_DRAIN_TIMEOUT_SECONDS`` to run to completion and answer; a
    handler that outlasts the drain is cancelled and given a bounded grace.
    Resistant handlers remain tracked for resource shutdown; they cannot
    prevent Gateway.close() from being reached. ``Gateway.close()``
    still owns turn and resource teardown.
    """
    requests: set[asyncio.Task[None]] = set()
    tail: asyncio.Task[None] | None = None

    def _retire(task: asyncio.Task[None]) -> None:
        requests.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:  # handles answer their own RpcErrors; this is a bug
            logger.exception("gateway request handler failed", exc_info=error)

    def start(line: str) -> None:
        nonlocal tail
        previous = tail

        async def chained() -> None:
            if previous is not None:
                # asyncio.wait (not awaiting the task directly) keeps a
                # predecessor's cancellation from propagating to this task.
                await asyncio.wait({previous})
            await gateway.handle_line(line)

        tail = asyncio.create_task(chained())
        requests.add(tail)
        tail.add_done_callback(_retire)

    while not gateway.closing:
        line = await queue.get()
        if line is None:
            break
        if _request_method(line) in _CONTROL_METHODS:
            # Preempt: awaited (not spawned) so shutdown is fully applied
            # before the loop decides whether to keep reading.
            await gateway.handle_line(line)
        else:
            start(line)

    pending = [task for task in requests if not task.done()]
    if pending:
        _done, pending = await asyncio.wait(pending, timeout=REQUEST_DRAIN_TIMEOUT_SECONDS)
    for task in pending:
        task.cancel()
    if pending:
        _done, pending = await asyncio.wait(pending, timeout=REQUEST_CANCEL_GRACE_SECONDS)
        if pending:
            from machina.core.runtime import runtime_services

            runtime = getattr(gateway, "runtime", runtime_services)
            for task in pending:
                runtime.track(task)
            logger.warning(
                "%d gateway request(s) resisted cancellation; resource shutdown continues",
                len(pending),
            )


async def _serve() -> None:
    from machina.gateway.server import Gateway

    # The UI launcher injects MACHINA_GATEWAY_TOKEN into the environment.
    # Standalone runs have no launcher: mint a token BEFORE the Gateway reads
    # the environment and disclose it on stderr — a silent mint would leave
    # every method except bare initialize answering unauthorized. Tokens
    # injected by the launcher are never re-printed.
    minted = ""
    if not os.environ.get("MACHINA_GATEWAY_TOKEN"):
        minted = secrets.token_hex(32)
        os.environ["MACHINA_GATEWAY_TOKEN"] = minted
    gateway = Gateway(_write)
    if minted:
        print(
            f"gateway auth token: {minted} (send it as params.token on every request)",
            file=sys.stderr,
            flush=True,
        )
    queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=_READ_QUEUE_MAX)
    _start_stdin_reader(asyncio.get_running_loop(), queue)
    try:
        await _dispatch_requests(gateway, queue)
    finally:
        await gateway.close()
        # Last frames (shutdown ack, close notifications) still sit in the
        # writer queue; drain them before the process exits.
        _stop_writer()


def main() -> None:
    logging.basicConfig(
        level=logging.WARNING, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s"
    )
    # asyncio.run() ends with an unbounded gather of every cancelled task.
    # A management handler that consumes cancellation would defeat the bounded
    # dispatcher/resource shutdown even after _serve() returned (F-25).
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    serving = loop.create_task(_serve())
    try:
        loop.run_until_complete(serving)
    except KeyboardInterrupt:
        serving.cancel()
        # Let _serve's finally reach resource teardown on an interactive stop.
        try:
            loop.run_until_complete(asyncio.wait({serving}, timeout=REQUEST_DRAIN_TIMEOUT_SECONDS))
        except KeyboardInterrupt:
            pass
    finally:

        async def retire_loop_tasks() -> None:
            tasks = {task for task in asyncio.all_tasks(loop) if task is not asyncio.current_task()}
            for task in tasks:
                task.cancel()
            if tasks:
                _done, pending = await asyncio.wait(tasks, timeout=REQUEST_CANCEL_GRACE_SECONDS)
                if pending:
                    logger.warning(
                        "closing gateway loop with %d cancellation-resistant task(s)", len(pending)
                    )
                    return
            finalizers = loop.create_task(loop.shutdown_asyncgens())
            _done, pending = await asyncio.wait({finalizers}, timeout=REQUEST_CANCEL_GRACE_SECONDS)
            if pending:
                finalizers.cancel()
                logger.warning("gateway async-generator finalization exceeded shutdown grace")
            elif not finalizers.cancelled():
                finalizers.exception()

        try:
            loop.run_until_complete(retire_loop_tasks())
        finally:
            loop.close()
            asyncio.set_event_loop(None)


if __name__ == "__main__":
    main()
