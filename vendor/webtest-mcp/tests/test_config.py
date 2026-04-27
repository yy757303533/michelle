"""测试配置加载"""

import pytest
from webtest_mcp.config import list_projects, load_project_config


def test_list_projects():
    projects = list_projects()
    assert "demo" in projects


def test_load_project_config():
    config = load_project_config("demo")
    assert "base_url" in config
    assert "the-internet" in config["base_url"]
