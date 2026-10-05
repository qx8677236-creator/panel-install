"""系统命令封装。只导出安全入口，不提供任意命令执行。"""

from app.commands.executor import CommandRejected, CommandResult, run_command

__all__ = ["CommandRejected", "CommandResult", "run_command"]
