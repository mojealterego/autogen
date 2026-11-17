"""Linux process workers for trusted scripts, not a security sandbox."""
from __future__ import annotations

import asyncio
import json
import math
import os
import signal
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class WorkerError(RuntimeError):
    """Worker failed or violated its bounded protocol."""


@dataclass(frozen=True)
class WorkerLimits:
    timeout: float = 30.0
    max_input: int = 65536
    max_output: int = 65536
    memory_bytes: int = 256 * 1024**2
    cpu_seconds: int = 15
    open_files: int = 64
    file_bytes: int = 1024**2

    def __post_init__(self) -> None:
        if type(self.timeout) not in (int, float) or not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError('invalid timeout')
        for value in (self.max_input, self.max_output, self.memory_bytes, self.cpu_seconds, self.open_files, self.file_bytes):
            if type(value) is not int or value <= 0:
                raise ValueError('invalid worker limit')
        if self.open_files < 8:
            raise ValueError('at least eight descriptors required')


_BOOTSTRAP = '''import resource, sys
path, memory, cpu, files, size = sys.argv[1:]
for kind, val in ((resource.RLIMIT_AS, memory), (resource.RLIMIT_CPU, cpu), (resource.RLIMIT_NOFILE, files), (resource.RLIMIT_FSIZE, size), (resource.RLIMIT_CORE, 0)):
    _, hard = resource.getrlimit(kind)
    v = int(val)
    if hard != resource.RLIM_INFINITY:
        v = min(v, hard)
    resource.setrlimit(kind, (v, v))
sys.argv = [path]
with open(path, 'rb') as source:
    code = compile(source.read(), path, 'exec')
exec(code, {'__name__': '__main__', '__file__': path, '__package__': None})
'''


async def _read_bounded(stream: asyncio.StreamReader, maximum: int) -> bytes:
    data = bytearray()
    while True:
        part = await stream.read(min(4096, maximum - len(data) + 1))
        if not part:
            return bytes(data)
        data.extend(part)
        if len(data) > maximum:
            raise WorkerError('worker output limit exceeded')


async def _send(proc: asyncio.subprocess.Process, data: bytes) -> None:
    assert proc.stdin is not None
    try:
        proc.stdin.write(data)
        await proc.stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        proc.stdin.close()


def _reject_constant(value: str) -> Any:
    raise ValueError('non-finite JSON constant')


class ProcessWorker:
    """Callable handler: one JSON value on stdin, one on stdout.

    Script and working directory are trusted configuration, never model output.
    Linux only. Limits are per-process; filesystem/network and escaped process
    groups are NOT isolated. Use an OS sandbox for untrusted code.
    """

    def __init__(self, script: str | Path, limits: WorkerLimits = WorkerLimits()) -> None:
        if sys.platform != 'linux':
            raise RuntimeError('process workers require Linux')
        self.script = Path(script).resolve(strict=True)
        if not self.script.is_file() or self.script.suffix != '.py':
            raise ValueError('worker must be a Python script')
        self.limits = limits

    async def __call__(self, inputs: Mapping[str, Any]) -> Any:
        limits = self.limits
        data = json.dumps(dict(inputs), allow_nan=False, ensure_ascii=False, separators=(',', ':')).encode()
        if len(data) > limits.max_input:
            raise WorkerError('worker input limit exceeded')
        loop = asyncio.get_running_loop()
        deadline = loop.time() + limits.timeout
        spawn = asyncio.create_task(asyncio.create_subprocess_exec(
            sys.executable, '-I', '-c', _BOOTSTRAP, str(self.script),
            str(limits.memory_bytes), str(limits.cpu_seconds), str(limits.open_files), str(limits.file_bytes),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True, cwd=str(self.script.parent), env={'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'},
            limit=min(65536, limits.max_output + 1),
        ))
        proc = None
        tasks: list[asyncio.Task[Any]] = []
        interrupted = False
        try:
            proc = await asyncio.wait_for(asyncio.shield(spawn), max(0, deadline - loop.time()))
            assert proc.stdout is not None and proc.stderr is not None
            tasks = [asyncio.create_task(_send(proc, data)), asyncio.create_task(_read_bounded(proc.stdout, limits.max_output)),
                     asyncio.create_task(_read_bounded(proc.stderr, limits.max_output)), asyncio.create_task(proc.wait())]
            _, output, _, code = await asyncio.wait_for(asyncio.gather(*tasks), max(0, deadline - loop.time()))
            if code != 0:
                raise WorkerError(f'worker exit status {code}')
            try:
                value = json.loads(output, parse_constant=_reject_constant)
                json.dumps(value, allow_nan=False)
                return value
            except (ValueError, UnicodeError, RecursionError) as exc:
                raise WorkerError('invalid JSON worker result') from exc
        finally:
            async def cleanup() -> None:
                nonlocal proc
                if proc is None:
                    try:
                        proc = await spawn
                    except Exception:
                        return
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                for task in tasks:
                    if not task.done():
                        task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                # Drain remaining buffered data after termination, including when
                # a paused StreamReader otherwise prevents transport completion.
                async def drain(stream: asyncio.StreamReader | None) -> None:
                    if stream is not None:
                        while await stream.read(4096):
                            pass
                if proc.stdin is not None:
                    proc.stdin.close()
                await asyncio.gather(drain(proc.stdout), drain(proc.stderr), proc.wait())
            finishing = asyncio.create_task(cleanup())
            while not finishing.done():
                try:
                    await asyncio.shield(finishing)
                except asyncio.CancelledError:
                    interrupted = True
            finishing.result()
            if interrupted:
                raise asyncio.CancelledError
