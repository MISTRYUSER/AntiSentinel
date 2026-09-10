"""Run actual Ragas ID metrics locally on a frozen retrieval report."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from antisentinel.evaluation.code_corpus import load_corpus
from antisentinel.evaluation.ragas_evaluation import samples_from_report, score_id_samples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path, default=ROOT)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    data = args.report.read_bytes()
    source = json.loads(data)
    corpus = load_corpus(args.repository, args.report.parent/'manifest.json')
    samples = samples_from_report(source, corpus)
    result = asyncio.run(score_id_samples(samples))
    result.update({'source_report_sha256': hashlib.sha256(data).hexdigest(), 'fingerprints': corpus.metadata,
                   'retrieval_execution_pass': source['execution_pass'], 'retrieval_quality_pass': source['quality_pass'],
                   'label_status': corpus.metadata['label_status']})
    args.output.mkdir(parents=True)
    (args.output/'samples.jsonl').write_text(''.join(json.dumps(s, ensure_ascii=False)+'\n' for s in samples))
    (args.output/'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    print(json.dumps({k: result[k] for k in ['ragas_version','execution_pass','quality_pass','modes','answer_metrics']}, ensure_ascii=False, indent=2))
    return 0 if result['execution_pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
