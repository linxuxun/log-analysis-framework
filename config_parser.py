"""
XML 配置文件解析器
"""
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List


class ConfigError(Exception):
    """配置解析错误"""
    pass


class SchemaValidationError(Exception):
    """JSON 格式验证错误"""
    pass


@dataclass
class ModuleConfig:
    """单个模块配置"""
    name: str
    script: str
    timeout: int = 60
    patterns: List[str] = field(default_factory=list)


@dataclass
class FrameworkConfig:
    """整个框架配置"""
    modules: List[ModuleConfig] = field(default_factory=list)


def parse_config(config_path: str) -> FrameworkConfig:
    """
    解析 XML 配置文件

    格式示例：
    <config>
      <modules>
        <module name="OPS" script="python3 scripts/ops_analyzer.py" timeout="60">
          <patterns>
            <pattern>logs/ops/*.log</pattern>
          </patterns>
        </module>
      </modules>
    </config>
    """
    try:
        tree = ET.parse(config_path)
        root = tree.getroot()
    except ET.ParseError as e:
        raise ConfigError(f"XML 解析失败: {e}")
    except FileNotFoundError:
        raise ConfigError(f"配置文件不存在: {config_path}")

    config = FrameworkConfig()

    modules_elem = root.find("modules")
    if modules_elem is None:
        raise ConfigError("配置文件中缺少 <modules> 节点")

    for module_elem in modules_elem.findall("module"):
        name = module_elem.get("name")
        script = module_elem.get("script")
        timeout_str = module_elem.get("timeout", "60")

        if not name:
            raise ConfigError("<module> 缺少 name 属性")
        if not script:
            raise ConfigError(f"<module name='{name}'> 缺少 script 属性")

        try:
            timeout = int(timeout_str)
        except ValueError:
            raise ConfigError(f"<module name='{name}'> timeout='{timeout_str}' 不是有效整数")

        patterns = []
        patterns_elem = module_elem.find("patterns")
        if patterns_elem is not None:
            for pattern_elem in patterns_elem.findall("pattern"):
                if pattern_elem.text:
                    patterns.append(pattern_elem.text.strip())

        config.modules.append(ModuleConfig(
            name=name,
            script=script,
            timeout=timeout,
            patterns=patterns
        ))

    return config


def validate_schema(data: dict) -> None:
    """
    验证模块输出的 JSON 格式
    要求必须包含 status 和 message 字段
    """
    if "status" not in data:
        raise SchemaValidationError("JSON 必须包含 'status' 字段")
    if "message" not in data:
        raise SchemaValidationError("JSON 必须包含 'message' 字段")

    valid_statuses = {"ok", "warning", "error", "info"}
    if data["status"] not in valid_statuses:
        raise SchemaValidationError(
            f"status 必须是 {valid_statuses} 之一，当前值: {data['status']}"
        )
