"""框架公共模块"""
from typing import Dict, List, Any, Optional
_all_modules_log_files = {}
_root_path = ""

def set_all_modules_log_files(files): global _all_modules_log_files; _all_modules_log_files = files
def get_all_modules_log_files(): return _all_modules_log_files
def get_module_log_files(name): return _all_modules_log_files.get(name)
def set_root_path(path): global _root_path; _root_path = path
def get_root_path(): return _root_path
