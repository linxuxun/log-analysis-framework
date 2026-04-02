"""
命令执行器
"""
import subprocess
import shutil
from dataclasses import dataclass
from typing import Optional


@dataclass
class CommandResult:
    """命令执行结果"""
    returncode: int
    stdout: str
    stderr: str


def execute_command(command: str, timeout: int = 60) -> CommandResult:
    """
    执行本地 shell 命令

    Args:
        command: 要执行的命令
        timeout: 超时时间（秒）

    Returns:
        CommandResult: 包含返回码、标准输出、标准错误
    """
    # 检查命令是否存在
    cmd_parts = command.split()
    if cmd_parts:
        program = cmd_parts[0]
        # 如果是 python3 开头的，检查后续 python 脚本
        if program == "python3" and len(cmd_parts) > 1:
            program = cmd_parts[1]

        # 查找可执行文件路径
        path = shutil.which(program)
        if path is None:
            return CommandResult(
                returncode=127,
                stdout="",
                stderr=f"命令未找到: {program}"
            )

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=None
        )
        return CommandResult(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr
        )
    except subprocess.TimeoutExpired:
        # 超时会被框架的 main.py 捕获，这里返回特殊值
        return CommandResult(
            returncode=-1,
            stdout="",
            stderr=f"命令执行超时（{timeout}s）"
        )
    except Exception as e:
        return CommandResult(
            returncode=-2,
            stdout="",
            stderr=f"执行异常: {str(e)}"
        )
