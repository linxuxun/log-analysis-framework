"""
日志文件收集器
"""
import os
import glob
from typing import List, Dict, Any


def collect_logs_by_pattern(root_path: str, patterns: List[str]) -> List[Dict[str, Any]]:
    """
    根据 glob 模式收集日志文件

    Args:
        root_path: 日志根目录
        patterns: glob 模式列表，如 ["logs/ops/*.log", "logs/ops/**/*.log"]

    Returns:
        文件信息列表，每个元素包含 {path, size, mtime}
    """
    results = []
    seen = set()  # 去重

    for pattern in patterns:
        # 拼接完整路径
        full_pattern = os.path.join(root_path, pattern)

        # 使用 glob 匹配
        matched = glob.glob(full_pattern, recursive=True)

        for filepath in matched:
            if filepath in seen:
                continue
            seen.add(filepath)

            try:
                stat = os.stat(filepath)
                results.append({
                    "path": filepath,
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                    "name": os.path.basename(filepath)
                })
            except OSError:
                continue

    # 按修改时间倒序
    results.sort(key=lambda x: x["mtime"], reverse=True)
    return results


def to_node_list_format(raw_files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    转换为 node_list 格式（供框架内部使用）
    每个文件只保留关键字段
    """
    return [
        {
            "file": f["name"],
            "path": f["path"],
            "size_kb": round(f["size"] / 1024, 2)
        }
        for f in raw_files
    ]
