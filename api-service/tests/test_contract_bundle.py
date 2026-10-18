import copy
import hashlib
import json
import shutil
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from api.artifacts import fetch, publish, verify, safe_path
from api.dimensions import BUNDLE_DIR, DIMENSION_NAMES
from api.model_bundle import ModelBundle, adapt_predictions
from api.service import app


def write_manifest(root, manifest):
    (root / 'manifest.json').write_text(json.dumps(manifest))


def rehash(root, manifest, name):
    p = root / name
    manifest['files'][name] = {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'size_bytes': p.stat().st_size}
    write_manifest(root, manifest)


@pytest.fixture
def bundle_copy(tmp_path):
    dst = tmp_path / 'bundle'
    shutil.copytree(BUNDLE_DIR, dst)
    return dst


def test_real_heldout_fixture_has_exact_ids_and_eight_targets():
    bundle = ModelBundle(BUNDLE_DIR)
    assert len(bundle.tiles) == 188
    assert set(bundle.scores) == {t.tile_id for t in bundle.tiles}
    assert len(DIMENSION_NAMES) == 8
    assert all(set(t.scores) == set(DIMENSION_NAMES) for t in bundle.scores.values())
    assert all(t.h3_index and t.geometry['type'] == 'Polygon' and t.row is None for t in bundle.tiles)


def test_matrix_adapter_respects_explicit_column_order():
    assert adapt_predictions({'ids': ['one'], 'dimensions': ['b', 'a'], 'values': [[.7, .2]]}, {'format': 'matrix'}, ['a', 'b']) == {'one': {'b': .7, 'a': .2}}


@pytest.mark.parametrize('payload', [
    {'ids': ['a', 'a'], 'dimensions': ['x'], 'values': [[.1], [.2]]},
    {'ids': ['a'], 'dimensions': ['x'], 'values': [[.1, .2]]},
    {'ids': ['a'], 'dimensions': ['wrong'], 'values': [[.1]]},
])
def test_adapter_rejects_duplicate_or_wrong_shape(payload):
    with pytest.raises(ValueError):
        adapt_predictions(payload, {'format': 'matrix'}, ['x'])


def test_tampered_file_fails_hash_check(bundle_copy):
    (bundle_copy / 'scores.json').write_text('[]')
    with pytest.raises(ValueError, match='checksum'):
        ModelBundle(bundle_copy)


def test_missing_id_fails_even_with_updated_checksum(bundle_copy):
    manifest = verify(bundle_copy)
    p = bundle_copy / 'scores.json'
    rows = json.loads(p.read_text()); rows.pop(); p.write_text(json.dumps(rows))
    rehash(bundle_copy, manifest, 'scores.json')
    with pytest.raises(ValueError, match='IDs'):
        ModelBundle(bundle_copy)


@pytest.mark.parametrize('value', [1.01, float('nan'), float('inf')])
def test_bad_model_score_rejected(bundle_copy, value):
    manifest = verify(bundle_copy)
    p = bundle_copy / 'scores.json'
    rows = json.loads(p.read_text()); rows[0]['scores']['shade'] = value; p.write_text(json.dumps(rows))
    rehash(bundle_copy, manifest, 'scores.json')
    with pytest.raises(ValueError):
        ModelBundle(bundle_copy)


def test_contract_version_mismatch_rejected(bundle_copy):
    m = verify(bundle_copy); m['schema_version'] = 'wrong'; write_manifest(bundle_copy, m)
    with pytest.raises(ValueError, match='schema'):
        ModelBundle(bundle_copy)


@pytest.mark.parametrize('name', ['../secret', '/tmp/file', 'a/../../file', 'a\\b', '.', 'a//b'])
def test_manifest_paths_cannot_escape_cache(tmp_path, name):
    with pytest.raises(ValueError):
        safe_path(tmp_path, name)


class Store:
    def __init__(self): self.objects = {}
    def get(self, name): return self.objects.get(name)
    def create(self, name, content):
        if name in self.objects and self.objects[name] != content: raise ValueError('immutable object')
        self.objects[name] = content


def test_publish_fetch_and_verified_cache(tmp_path):
    store = Store(); m = publish(BUNDLE_DIR, store)
    assert list(store.objects)[-1] == 'manifest.json'
    dest = tmp_path / 'cache'
    assert fetch(dest, store) == m
    assert fetch(dest, store) == m
    assert verify(dest) == m
    (dest / 'scores.json').write_text('broken')
    with pytest.raises(ValueError): fetch(dest, store)


def test_corrupt_remote_fetch_never_leaves_valid_looking_cache(tmp_path):
    store = Store(); publish(BUNDLE_DIR, store); store.objects['scores.json'] = b'broken'
    dest = tmp_path / 'cache'
    with pytest.raises(ValueError): fetch(dest, store)
    assert not dest.exists()
    assert not list(tmp_path.glob('.artifact-*'))


def test_immutable_publication_rejects_new_manifest_before_upload(bundle_copy):
    store = Store(); publish(BUNDLE_DIR, store)
    before = copy.deepcopy(store.objects)
    m = verify(bundle_copy); m['artifact_version'] = 'changed'; write_manifest(bundle_copy, m)
    with pytest.raises(ValueError): publish(bundle_copy, store)
    assert store.objects == before


def test_offline_api_contract_and_geographic_scores(monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER', 'mock')
    with TestClient(app) as client:
        meta = client.get('/capabilities').json()
        response = client.post('/score', json={'text': 'Shade if possible, avoid traffic'})
        assert response.status_code == 200
        result = response.json()
        assert result['schema_version'] == meta['schema_version']
        assert len(result['features']) == 188 and result['heatmap'] is None
        assert result['prediction_source'] == 'saved_heldout_baseline'
        assert result['weights']['traffic_intensity'] == -1
        assert result['weights']['shade'] == .6
        assert result['rmse_source'].startswith('placeholder')
        unknown = client.post('/score', json={'text': 'quiet', 'region_id': 'missing'})
        assert unknown.status_code == 404
        unsupported = client.post('/score', json={'text': 'Water is a must'}).json()
        assert unsupported['agent']['preference']['unsupported'] == ['water']
        assert all(v == 0 for v in unsupported['weights'].values())


def test_new_bundle_can_change_target_count_without_app_code_changes(bundle_copy):
    import os
    import subprocess
    import sys
    m = verify(bundle_copy)
    schema_path = bundle_copy / 'schema.json'
    schema = json.loads(schema_path.read_text())
    schema['schema_version'] = 'two-targets-test'
    schema['dimensions'] = {k: v for k, v in schema['dimensions'].items() if k in ['shade', 'quietness']}
    schema_path.write_text(json.dumps(schema))
    m['schema_version'] = schema['schema_version']
    rehash(bundle_copy, m, 'schema.json')
    p = bundle_copy / 'scores.json'
    rows = json.loads(p.read_text())
    for row in rows:
        row['scores'] = {k: v for k, v in row['scores'].items() if k in schema['dimensions']}
    p.write_text(json.dumps(rows)); rehash(bundle_copy, m, 'scores.json')
    env = {**os.environ, 'MODEL_PROVIDER': 'bundle', 'MODEL_BUNDLE_DIR': str(bundle_copy), 'LLM_PROVIDER': 'mock', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
    code = "from api.pipeline import run_score,capabilities; r=run_score('quiet and shade'); assert len(capabilities()['dimensions'])==2; assert len(r.features)==188; assert set(r.weights)=={'shade','quietness'}"
    subprocess.run([sys.executable, '-c', code], env=env, check=True, capture_output=True, text=True)


def test_legacy_simulated_mode_remains_explicit():
    import os
    import subprocess
    import sys
    env = {**os.environ, 'MODEL_PROVIDER': 'legacy', 'LLM_PROVIDER': 'mock', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
    env.pop('MODEL_SCHEMA_PATH', None)
    code = "from api.pipeline import run_score,capabilities; r=run_score('water'); assert r.prediction_source=='simulated'; assert r.rows==100 and r.cols==100; assert len(capabilities()['dimensions'])==20"
    subprocess.run([sys.executable, '-c', code], env=env, check=True, capture_output=True, text=True)


def test_declared_score_range_scales_thresholds(tmp_path):
    import os
    import subprocess
    import sys
    from api.dimensions import SPEC
    schema = copy.deepcopy(SPEC); schema['score_range'] = [1, 5]
    p = tmp_path / 'range.json'; p.write_text(json.dumps(schema))
    env = {**os.environ, 'MODEL_SCHEMA_PATH': str(p), 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
    code = "from api.score_math import combine; from api.schemas import TileScore; from api.dimensions import DIMENSION_NAMES; tiles=[TileScore(tile_id=str(i),row=0,col=i,scores={d:(v if d=='shade' else 1.0) for d in DIMENSION_NAMES}) for i,v in enumerate([1.,1.2,1.6])]; heatmap,stats=combine(tiles,{'shade':1},['shade']); assert heatmap[0][:2]==[None,None] and heatmap[0][2] is not None; assert stats.n_excluded==2"
    subprocess.run([sys.executable, '-c', code], env=env, check=True, capture_output=True, text=True)


def test_negative_required_dimension_cannot_be_treated_as_minimum_gate():
    from api.schemas import Preference
    with pytest.raises(ValueError, match='positive'):
        Preference.model_validate({'weights': [{'dimension': 'shade', 'reason': 'avoid shade', 'weight': -1}], 'must_have': ['shade']})
