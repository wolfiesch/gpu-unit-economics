from pathlib import Path

from gpu_econ.registry import _registry_dir


def test_registry_dir_falls_back_to_working_tree_for_installed_package(
    tmp_path: Path,
) -> None:
    installed_module = tmp_path / "site-packages" / "gpu_econ" / "registry.py"
    app_root = tmp_path / "app"
    registry_dir = app_root / "data" / "registry"
    registry_dir.mkdir(parents=True)
    (registry_dir / "sources.csv").touch()

    assert _registry_dir(installed_module, app_root) == registry_dir
