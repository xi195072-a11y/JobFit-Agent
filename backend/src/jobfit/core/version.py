"""应用版本（单一事实来源，§38）。

从已安装的 package metadata 读取版本（pyproject.toml 的 `version`），
避免 package.json / pyproject.toml / README / health 互相矛盾。
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

_FALLBACK = "0.1.0"


def app_version() -> str:
    """返回应用版本；未安装（如直接以源码运行）时回退到与 pyproject 一致的常量。"""
    try:
        return _pkg_version("jobfit")
    except PackageNotFoundError:
        return _FALLBACK


__all__ = ["app_version"]
