from pathlib import Path

import pytest

import gpu_econ.registry as registry
from gpu_econ.registry import ModelRecord, _registry_dir, _rows


def test_registry_dir_falls_back_to_working_tree_for_installed_package(
    tmp_path: Path,
) -> None:
    installed_module = tmp_path / "site-packages" / "gpu_econ" / "registry.py"
    app_root = tmp_path / "app"
    registry_dir = app_root / "data" / "registry"
    registry_dir.mkdir(parents=True)
    (registry_dir / "sources.csv").touch()

    assert _registry_dir(installed_module, app_root) == registry_dir


def test_rows_rejects_outdated_registry_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "models.csv").write_text("id,label\nlegacy,Legacy\n", encoding="utf-8")
    monkeypatch.setattr(registry, "REGISTRY_DIR", tmp_path)

    with pytest.raises(
        ValueError,
        match=r"models\.csv is missing required columns for registry .*active_parameters_b",
    ):
        _rows("models.csv", ModelRecord)
