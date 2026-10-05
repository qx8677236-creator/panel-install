"""安全的系统命令执行封装。

设计约束：
- 调用方只能传参数列表，传入整段 shell 字符串会直接拒绝。
- 始终 subprocess，且 shell=False，元字符不会被外壳解释。
- 可执行文件必须命中白名单，并且解析到固定的系统目录。
- 本模块没有、也不应该被接成「在网页里跑任意命令」的接口。
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

# 新增命令前要单独评审。不要把 sh、bash、sudo、rm 放进来。
ALLOWED_COMMANDS = frozenset({"uname"})

# 防止 PATH 被劫持到非系统目录。
_TRUSTED_PREFIXES = ("/bin/", "/usr/bin/", "/usr/sbin/", "/sbin/")

# 即使 shell=False，也拒绝明显的外壳元字符，避免以后误用。
_FORBIDDEN_CHARS = set(";|&$`<>\\()\n\r\x00")

MAX_ARGS = 16
MAX_ARG_LEN = 128
MAX_OUTPUT = 64 * 1024
MAX_TIMEOUT = 15


class CommandRejected(Exception):
    """命令未通过安全检查，没有执行。"""


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


def run_command(argv: Sequence[str], *, timeout: float = 5) -> CommandResult:
    """执行一条白名单命令。

    argv 示例：["uname", "-s", "-r"]。
    不要拼接字符串，也不要自行打开 shell。
    """
    if isinstance(argv, (str, bytes, bytearray)) or not isinstance(argv, Sequence):
        raise CommandRejected("禁止传入 shell 字符串，请使用参数列表")
    if timeout <= 0 or timeout > MAX_TIMEOUT:
        raise CommandRejected("超时时间不在允许范围内")

    items = list(argv)
    if not items or len(items) > MAX_ARGS:
        raise CommandRejected("参数数量不合法")

    command = items[0]
    args = items[1:]
    if not isinstance(command, str) or command not in ALLOWED_COMMANDS:
        raise CommandRejected("命令不在白名单内")
    if "/" in command or command.startswith("-") or ".." in command:
        raise CommandRejected("命令名不合法")

    for arg in args:
        _validate_arg(arg)

    resolved = _resolve_binary(command)
    try:
        completed = subprocess.run(
            [str(resolved), *args],
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise CommandRejected("命令执行超时") from exc
    except OSError as exc:
        raise CommandRejected("命令无法执行") from exc

    return CommandResult(
        argv=(command, *args),
        returncode=completed.returncode,
        stdout=(completed.stdout or "")[:MAX_OUTPUT],
        stderr=(completed.stderr or "")[:MAX_OUTPUT],
    )


def _validate_arg(arg: object) -> None:
    if not isinstance(arg, str) or arg == "":
        raise CommandRejected("参数必须是非空字符串")
    if len(arg) > MAX_ARG_LEN:
        raise CommandRejected("参数过长")
    if any(ch in _FORBIDDEN_CHARS for ch in arg):
        raise CommandRejected("参数包含不允许的字符")


def _resolve_binary(command: str) -> Path:
    found = shutil.which(command)
    if not found:
        raise CommandRejected("系统中不存在该命令")
    resolved = Path(found).resolve()
    if not resolved.is_file() or not resolved.is_absolute():
        raise CommandRejected("命令路径无效")
    if resolved.name != command:
        raise CommandRejected("命令名与实际文件不一致")
    location = resolved.as_posix()
    if not location.startswith(_TRUSTED_PREFIXES):
        raise CommandRejected("命令路径不在系统目录内")
    return resolved
