"""
Automated import verification across all domain, application, and infrastructure files.
"""

import importlib
import pkgutil
import pytest


MODULE_ROOTS = [
    "app.modules.auth",
    "app.modules.portfolio",
    "app.modules.news_intelligence",
    "app.modules.market_intelligence",
    "app.modules.market_reports",
    "app.db",
    "app.core",
    "app.cache",
    "app.api",
    "workers",
]


def get_all_submodules(root_package_name: str):
    submodules = [root_package_name]
    try:
        pkg = importlib.import_module(root_package_name)
        if hasattr(pkg, "__path__"):
            for _, name, is_pkg in pkgutil.walk_packages(pkg.__path__, prefix=f"{root_package_name}."):
                submodules.append(name)
    except Exception as e:
        print(f"Error walking {root_package_name}: {e}")
    return submodules


@pytest.mark.parametrize("root", MODULE_ROOTS)
def test_module_imports_without_error(root: str):
    submodules = get_all_submodules(root)
    assert len(submodules) > 0

    for sub in submodules:
        try:
            mod = importlib.import_module(sub)
            assert mod is not None
        except Exception as e:
            pytest.fail(f"Failed to import {sub}: {e}")
