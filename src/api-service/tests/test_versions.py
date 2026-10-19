"""Version selection must keep score/model/region identity bound per request."""
import copy
import hashlib
import json
from pathlib import Path
import shutil

from fastapi.testclient import TestClient
import pytest
from api.dimensions import BUNDLE_DIR
from api.model_bundle import get_bundle
from api.service import app


@pytest.fixture
def version_catalog(tmp_path, monkeypatch):
    entries = []
    for name, model, shade in [('pilot-a', 'model-a', .1), ('pilot-b', 'model-b', .9)]:
        target = tmp_path / name
        shutil.copytree(BUNDLE_DIR, target)
        manifest = json.loads((target / 'manifest.json').read_text())
        manifest.update({'artifact_version': name, 'model_version': model, 'region_id': name})
        scores = json.loads((target / 'scores.json').read_text())
        for row in scores: row['scores']['shade'] = shade
        (target / 'scores.json').write_text(json.dumps(scores))
        manifest['files']['scores.json'] = {'size_bytes': (target / 'scores.json').stat().st_size,
                                           'sha256': hashlib.sha256((target / 'scores.json').read_bytes()).hexdigest()}
        (target / 'manifest.json').write_text(json.dumps(manifest))
        entries.append({'id': name, 'label': name, 'bundle_path': name,
                        'manifest_sha256': hashlib.sha256((target / 'manifest.json').read_bytes()).hexdigest()})
    catalog = tmp_path / 'catalog.json'
    catalog.write_text(json.dumps({'format_version': 'greenpath-serving-catalog-v1', 'default_version': 'pilot-a', 'versions': entries}))
    monkeypatch.setenv('MODEL_ARTIFACTS_ROOT', str(tmp_path))
    monkeypatch.setenv('MODEL_CATALOG_PATH', str(catalog))
    monkeypatch.setenv('LLM_PROVIDER', 'mock')
    get_bundle.cache_clear()
    yield tmp_path, catalog
    get_bundle.cache_clear()


def test_requests_choose_versions_without_global_model_mutation(version_catalog):
    with TestClient(app) as client:
        versions = client.get('/versions').json()
        assert {'pilot-a', 'pilot-b'} <= {v['id'] for v in versions['versions'] if v['available']}
        first = client.post('/score', json={'text': 'shade', 'version_id': 'pilot-a'}).json()
        second = client.post('/score', json={'text': 'shade', 'version_id': 'pilot-b'}).json()
        again = client.post('/score', json={'text': 'shade', 'version_id': 'pilot-a'}).json()
        assert first['model_version'] == again['model_version'] == 'model-a'
        assert second['model_version'] == 'model-b'
        assert first['features'][0]['properties']['scores']['shade'] == .1
        assert second['features'][0]['properties']['scores']['shade'] == .9
        assert client.get('/capabilities').json()['version_id'] == 'pilot-a'


def test_unknown_version_does_not_fall_back(version_catalog):
    with TestClient(app) as client:
        assert client.get('/capabilities?version_id=missing').status_code == 404
        assert client.post('/score', json={'text': 'shade', 'version_id': 'missing'}).status_code == 404


def test_manifest_pin_rejects_reused_identity(version_catalog):
    root, _ = version_catalog
    with TestClient(app) as client:
        assert client.get('/preview?version_id=pilot-b').status_code == 200
        path = root / 'pilot-b/manifest.json'
        manifest = json.loads(path.read_text()); manifest['model_version'] = 'unexpected'
        path.write_text(json.dumps(manifest))
        assert client.get('/preview?version_id=pilot-b').status_code == 503


def test_preview_never_calls_preference_provider(version_catalog, monkeypatch):
    import api.llm
    monkeypatch.setattr(api.llm, 'ask', lambda *a, **k: pytest.fail('Preview called the preference model'))
    with TestClient(app) as client:
        response = client.get('/preview?version_id=pilot-b')
        assert response.status_code == 200
        assert response.json()['preview'] is True
        assert response.json()['agent']['attempts'] == []
        assert response.json()['version_id'] == 'pilot-b'
