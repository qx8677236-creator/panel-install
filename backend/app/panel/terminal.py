"""网页终端。只启动当前面板用户的 bash，不切换到 root。"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import pwd
import signal
import struct
import termios
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.auth.security import TokenError, decode_token
from app.auth.store import load_admin
from app.panel.settings import terminal_config

router = APIRouter()
_active = 0
_limit = 2
_guard: asyncio.Lock | None = None
_BASH = Path("/bin/bash")


def _lock() -> asyncio.Lock:
    global _guard
    if _guard is None:
        _guard = asyncio.Lock()
    return _guard


def _shell_path() -> Path | None:
    if not _BASH.exists():
        return None
    resolved = _BASH.resolve()
    if resolved.as_posix() not in {"/bin/bash", "/usr/bin/bash"} or not resolved.is_file():
        return None
    return resolved


def _window(fd: int, rows: object, cols: object) -> None:
    try:
        row_count = max(8, min(int(rows), 80))
        col_count = max(20, min(int(cols), 240))
    except (TypeError, ValueError):
        return
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", row_count, col_count, 0, 0))


def _rcfile() -> str:
    path = Path(__file__).resolve().parents[2] / "data" / "panel" / "terminal.bashrc"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[ -f /etc/bash_completion ] && . /etc/bash_completion\n", encoding="utf-8")
    path.chmod(0o644)
    return path.as_posix()


async def _take() -> bool:
    global _active
    async with _lock():
        if _active >= _limit:
            return False
        _active += 1
        return True


async def _give() -> None:
    global _active
    async with _lock():
        _active = max(0, _active - 1)


async def _open_shell(master: int, slave: int):
    account = pwd.getpwuid(os.getuid())
    env = {
        "HOME": account.pw_dir,
        "USER": account.pw_name,
        "LOGNAME": account.pw_name,
        "SHELL": "/bin/bash",
        "TERM": "xterm-256color",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
    }
    argv = ["/bin/bash", "--noprofile", "--norc", "-i"]
    if terminal_config()["use_completion"]:
        argv = ["/bin/bash", "--noprofile", "--rcfile", _rcfile(), "-i"]
    stdin_fd, stdout_fd, stderr_fd = os.dup(slave), os.dup(slave), os.dup(slave)
    _window(master, 24, 80)
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=stdin_fd,
            stdout=stdout_fd,
            stderr=stderr_fd,
            cwd=account.pw_dir,
            env=env,
            start_new_session=True,
        )
    finally:
        os.close(stdin_fd)
        os.close(stdout_fd)
        os.close(stderr_fd)
    return process


async def _read(fd: int) -> bytes:
    def once() -> bytes:
        import select

        ready, _, _ = select.select([fd], [], [], 0.4)
        if not ready:
            return b""
        try:
            return os.read(fd, 4096)
        except OSError:
            return b""

    return await asyncio.to_thread(once)


async def _stop(process) -> None:
    if process is None or process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except OSError:
        process.kill()
    try:
        await asyncio.wait_for(process.wait(), timeout=2)
    except asyncio.TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            process.kill()
        await process.wait()


@router.websocket("/ws/terminal")
async def terminal_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    if _shell_path() is None:
        await websocket.close(code=4410)
        return
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=8)
        message = json.loads(raw)
        token = message.get("token") if isinstance(message, dict) else None
        payload = decode_token(token, "access")
        if payload["sub"] != load_admin()["username"]:
            raise TokenError("用户不匹配")
    except (asyncio.TimeoutError, TokenError, TypeError, ValueError, WebSocketDisconnect):
        await websocket.close(code=4401)
        return
    if not await _take():
        await websocket.close(code=4409)
        return
    master, slave = os.openpty()
    process = None
    reader = None
    try:
        process = await _open_shell(master, slave)
        os.close(slave)
        slave = -1
        await websocket.send_json({"type": "ready", "user": pwd.getpwuid(os.getuid()).pw_name})

        async def pump() -> None:
            while process.returncode is None:
                chunk = await _read(master)
                if not chunk:
                    if process.returncode is not None:
                        return
                    continue
                await websocket.send_bytes(chunk)

        reader = asyncio.create_task(pump())
        deadline = asyncio.get_running_loop().time() + 7200
        while process.returncode is None and asyncio.get_running_loop().time() < deadline:
            try:
                incoming = await asyncio.wait_for(websocket.receive_json(), timeout=1)
            except asyncio.TimeoutError:
                continue
            except (WebSocketDisconnect, RuntimeError, json.JSONDecodeError):
                break
            if not isinstance(incoming, dict):
                continue
            if incoming.get("type") == "input":
                data = incoming.get("data")
                if not isinstance(data, str) or len(data) > 4096:
                    continue
                try:
                    os.write(master, data.encode("utf-8", errors="replace"))
                except OSError:
                    break
            elif incoming.get("type") == "resize":
                try:
                    _window(master, incoming.get("rows"), incoming.get("cols"))
                except OSError:
                    break
    finally:
        if reader is not None:
            reader.cancel()
            try:
                await reader
            except (asyncio.CancelledError, Exception):
                pass
        await _stop(process)
        if slave >= 0:
            os.close(slave)
        os.close(master)
        await _give()
        try:
            await websocket.close()
        except Exception:
            return
