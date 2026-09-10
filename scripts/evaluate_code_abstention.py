"""Replay a frozen cosine-threshold experiment without model requests."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from antisentinel.evaluation.abstention import compare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--development', required=True, type=Path)
    parser.add_argument('--heldout', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    development, heldout = args.development.read_bytes(), args.heldout.read_bytes()
    report = compare(json.loads(development), json.loads(heldout))
    report['input_sha256'] = {'development': hashlib.sha256(development).hexdigest(), 'heldout': hashlib.sha256(heldout).hexdigest()}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0  # Execution success is distinct from a rejected policy.


if __name__ == '__main__':
    raise SystemExit(main())
