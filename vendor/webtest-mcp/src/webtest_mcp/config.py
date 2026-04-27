"""项目配置 - project.yaml"""

import os
from pathlib import Path
from typing import Any

import yaml


def get_projects_dir() -> Path:
    """获取 projects 目录。可通过环境变量 WEBTEST_PROJECTS_DIR 覆盖。"""
    env_dir = os.environ.get("WEBTEST_PROJECTS_DIR")
    if env_dir and Path(env_dir).exists():
        return Path(env_dir)
    current = Path(__file__).resolve().parent.parent.parent
    candidates = [
        current / "projects",
        Path.cwd() / "projects",
        Path.cwd() / "webtest-mcp-server" / "projects",
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


def load_project_config(project_key: str) -> dict[str, Any]:
    """加载 project.yaml"""
    projects_dir = get_projects_dir()
    project_dir = projects_dir / project_key
    config_path = project_dir / "project.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"项目 {project_key} 的 project.yaml 不存在: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("project.yaml 必须是 YAML 对象")
    data["_project_dir"] = str(project_dir)
    return data


def list_projects() -> list[str]:
    """列出所有已配置的项目"""
    projects_dir = get_projects_dir()
    if not projects_dir.exists():
        return []
    result = []
    for item in projects_dir.iterdir():
        if item.is_dir() and not item.name.startswith("."):
            if (item / "project.yaml").exists():
                result.append(item.name)
    return sorted(result)
