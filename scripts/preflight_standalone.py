"""Read-only gate before downloading or starting the local Milvus Case."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess


def inspect_environment(data_root, *, min_free_gib=10):
    if min_free_gib < 1:
        raise ValueError('positive disk headroom required')
    location = Path(data_root).resolve()
    while not location.exists():
        location = location.parent
    free = shutil.disk_usage(location).free
    result = {'ready': False, 'host_disk_free_bytes': free,
        'required_headroom_bytes': min_free_gib * 1024**3,
        'production_capacity_verified': False, 'errors': []}
    if free < result['required_headroom_bytes']:
        result['errors'].append('insufficient_host_disk_headroom')
        return result
    try:
        process = subprocess.run(['docker', 'info', '--format', '{{json .}}'],
            capture_output=True, text=True, timeout=10)
        if process.returncode:
            result['errors'].append('docker_engine_unavailable')
            return result
        info = json.loads(process.stdout)
        result['docker'] = {key: info.get(key) for key in ('ServerVersion', 'Architecture', 'NCPU', 'MemTotal')}
        if (info.get('NCPU') or 0) < 4 or (info.get('MemTotal') or 0) < 8_000_000_000:
            result['errors'].append('insufficient_vm_resources')
    except (OSError, subprocess.TimeoutExpired, ValueError):
        result['errors'].append('docker_engine_unavailable')
    result['ready'] = not result['errors']
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-root', type=Path, required=True)
    args = parser.parse_args()
    result = inspect_environment(args.data_root)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['ready'] else 1)
