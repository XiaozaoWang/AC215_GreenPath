"""Do not let a developer's selected local results alter unit-test defaults."""
import pytest
from api.model_bundle import get_bundle


@pytest.fixture(autouse=True)
def isolated_result_catalog(tmp_path, monkeypatch):
    monkeypatch.setenv('MODEL_CATALOG_PATH', str(tmp_path / 'no-local-catalog.json'))
    get_bundle.cache_clear()
    yield
    get_bundle.cache_clear()
