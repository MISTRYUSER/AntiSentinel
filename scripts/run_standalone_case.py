"""Acceptance against an explicitly owned, local Compose Standalone stack."""
import argparse
import json
import logging
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import time
import grpc
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT))
from pymilvus import MilvusClient
from scripts.run_retrieval_coordinator_case import run


def is_auth_rejection(error):
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, grpc.RpcError) and error.code() in {grpc.StatusCode.UNAUTHENTICATED, grpc.StatusCode.PERMISSION_DENIED}:
            return True
        error = error.__cause__ or error.__context__
    return False


def execute(control_path, *, resume_bootstrap=False):
    logging.disable(logging.CRITICAL)
    control_path = Path(control_path).resolve()
    root = control_path.parent
    def stage(name):
        (root/'stage.json').write_text(json.dumps({'stage': name}))
    control = json.loads(control_path.read_text())
    if not control['project'].startswith('antisentinel-milvus-') or control['uri'] != 'http://127.0.0.1:29530':
        raise ValueError('case requires its dedicated local Compose project')
    if Path(control['compose']).resolve() != ROOT/'deploy/milvus/compose.yaml':
        raise ValueError('unexpected Compose file')
    output = root/'runtime-case'
    if output.exists() or ((root/'milvus-token').exists() and not resume_bootstrap):
        raise ValueError('case already initialized; do not reset an existing credential')
    if resume_bootstrap and not (root/'milvus-token').exists():
        raise ValueError('no bootstrap credential to resume')
    compose = ['docker', 'compose', '--project-name', control['project'], '--env-file', control['env_file'], '-f', control['compose']]
    receipt = json.loads((root/'image-receipt.json').read_text())
    release = json.loads((root/'release-tag.json').read_text())
    if receipt['source_manifest'] != 'sha256:d0f1645d57e341701f80b92b8d455017c739db89732a5ec3ffb3ba605dee13cd' or release['object']['sha'] != '658cbd16899bb715a17d4d6f727531a376678ca6':
        raise ValueError('unexpected release provenance')
    container_id = subprocess.run(compose+['ps', '-q', 'standalone'], check=True, capture_output=True, text=True, timeout=10).stdout.strip()
    container = json.loads(subprocess.run(['docker', 'inspect', container_id], check=True, capture_output=True, text=True, timeout=10).stdout)[0]
    if container['Image'] != receipt['local_image_id']:
        raise ValueError('running image differs from verified import')
    stage('bootstrap_connection')
    token = (root/'milvus-token').read_text() if resume_bootstrap else 'root:Milvus'
    client = MilvusClient(uri=control['uri'], token=token, timeout=5)
    try:
        version = client.get_server_version(timeout=5)
        if version != '3.0-20260902-658cbd1689' or client.list_collections(timeout=5):
            raise ValueError('requires empty Milvus v3.0.1 acceptance instance')
        if not resume_bootstrap:
            password = 'Aa9!' + secrets.token_hex(20)
            token = 'root:' + password
            # Preserve the recovery credential before its remote mutation, never print it.
            descriptor = os.open(root/'milvus-token', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w') as stream:
                stream.write(token)
            stage('rotate_case_credential')
            client.update_password(user_name='root', old_password='Milvus', new_password=password, timeout=5)
    finally:
        client.close()
    rejected = False
    stage('reject_invalid_credential')
    invalid = None
    try:
        invalid = MilvusClient(uri=control['uri'], token='root:wrong-case-password', timeout=3)
        invalid.list_collections(timeout=3)
    except Exception as error:
        rejected = is_auth_rejection(error)
    finally:
        if invalid is not None:
            invalid.close()
    if not rejected:
        raise RuntimeError('authentication rejection not verified')
    lifecycle = {}
    def restart():
        # Check the server before a recovered worker can repair missing vectors.
        with sqlite3.connect(output/'facts.sqlite') as database:
            database.row_factory = sqlite3.Row
            expected = {}
            for task in database.execute('SELECT * FROM code_embedding_tasks'):
                document = json.loads(task['document_json'])
                expected[task['point_id']] = {
                    **{key: document[key] for key in ('repository_id', 'snapshot_id', 'published_generation', 'commit_sha', 'node_id', 'chunk_id', 'path', 'source_hash')},
                    **{key: task[key] for key in ('document_id', 'input_hash', 'model_revision', 'dimension', 'template_revision', 'projection_revision')}}
        stage('stop_case_server')
        subprocess.run(compose + ['stop', 'standalone'], check=True, capture_output=True, timeout=25)
        stage('outage_probe')
        begin = time.monotonic()
        unreachable = False
        probe = None
        try:
            probe = MilvusClient(uri=control['uri'], token=token, timeout=2)
            probe.list_collections(timeout=2)
        except Exception:
            unreachable = True
        finally:
            if probe is not None:
                probe.close()
        lifecycle['stopped_server_rejected'] = unreachable
        lifecycle['outage_probe_ms'] = (time.monotonic()-begin)*1000
        stage('restart_case_server')
        subprocess.run(compose + ['start', 'standalone'], check=True, capture_output=True, timeout=20)
        deadline = time.monotonic()+60
        while time.monotonic() < deadline:
            try:
                with urlopen('http://127.0.0.1:29091/healthz', timeout=2) as response:
                    if response.status == 200:
                        lifecycle['server_restarted'] = True
                        restored = MilvusClient(uri=control['uri'], token=token, timeout=5)
                        try:
                            collections = restored.list_collections(timeout=5)
                            if len(collections) != 1 or len(expected) != 2:
                                raise RuntimeError('unexpected restored collection or task count')
                            restored.load_collection(collections[0], timeout=20)
                            values = restored.get(collections[0], ids=list(expected),
                                output_fields=['point_id', *next(iter(expected.values()))],
                                consistency_level='Strong', timeout=10)
                            valid = ({item['point_id'] for item in values} == set(expected)
                                and all(all(item.get(key) == value for key, value in expected[item['point_id']].items()) for item in values))
                            lifecycle['persisted_before_worker_repair'] = valid
                            if not valid:
                                raise RuntimeError('server persistence did not match SQLite')
                        finally:
                            restored.close()
                        return
            except OSError:
                pass
            time.sleep(.5)
        raise TimeoutError('standalone restart deadline exceeded')
    stage('application_runtime')
    result = run(output, milvus_uri=control['uri'], token=token, between_starts=restart)
    checks = {'authenticated_connection': True, 'invalid_credential_rejected': rejected,
        'server_restarted': lifecycle.get('server_restarted', False),
        'persisted_before_worker_repair': lifecycle.get('persisted_before_worker_repair', False),
        'outage_rejected_within_budget': lifecycle.get('stopped_server_rejected', False) and lifecycle['outage_probe_ms'] <= 5000,
        'application_case': result['case_pass']}
    report = {'deployment_release': 'v3.0.1', 'release_commit': release['object']['sha'],
        'verified_image_id': receipt['local_image_id'], 'server_version': version, 'checks': checks, 'lifecycle': lifecycle,
        'case_pass': all(checks.values()), 'runtime_report': str(output/'report.json'),
        'production_capacity_verified': False, 'root_used_for_isolated_case_only': True,
        'external_model_calls': 0}
    (root/'standalone-report.json').write_text(json.dumps(report, indent=2))
    stage('completed')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--control', required=True)
    parser.add_argument('--resume-bootstrap', action='store_true')
    args = parser.parse_args()
    try:
        report = execute(args.control, resume_bootstrap=args.resume_bootstrap)
        print(json.dumps(report, indent=2))
        raise SystemExit(0 if report['case_pass'] else 1)
    except Exception as error:
        failure = {'case_pass': False, 'error_type': type(error).__name__, 'reason': 'see controlled stage outputs; credential-bearing exception text suppressed'}
        (Path(args.control).parent/'failure.json').write_text(json.dumps(failure, indent=2))
        print(json.dumps(failure))
        raise SystemExit(1)
