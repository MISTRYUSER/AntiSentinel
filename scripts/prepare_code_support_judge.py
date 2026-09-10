"""Prepare local, label-free model inputs; this command never calls a model."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from antisentinel.evaluation.code_corpus import load_corpus
from antisentinel.evaluation.support_judge import make_packet, messages_for, SYSTEM_PROMPT, messages_for_ranges, RANGE_SYSTEM_PROMPT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', required=True, type=Path)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--retrieval-rows', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--citation-format', choices=['ranges', 'quotes'], default='ranges')
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    corpus = load_corpus(args.repository, args.manifest)
    documents = {d.document_id: d for d in corpus.documents}
    queries = {q['id']: q for q in corpus.queries}
    data = args.retrieval_rows.read_bytes()
    rows = json.loads(data)
    if len(rows) != len(queries) or {r['id'] for r in rows} != set(queries):
        raise ValueError('query coverage mismatch')
    packets = [make_packet(queries[r['id']], r['baseline_ids'], documents) for r in rows]
    formatter = messages_for_ranges if args.citation_format == 'ranges' else messages_for
    prompt = RANGE_SYSTEM_PROMPT if args.citation_format == 'ranges' else SYSTEM_PROMPT
    report = {'queries': len(packets), 'candidate_occurrences': sum(len(p['candidates']) for p in packets),
        'disclosed_source_bytes': sum(p['disclosed_bytes'] for p in packets),
        'incomplete_queries': sum(p['incomplete'] for p in packets),
        'external_calls': 0, 'fingerprints': corpus.metadata,
        'retrieval_rows_sha256': hashlib.sha256(data).hexdigest(),
        'citation_format': args.citation_format,
        'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest()}
    args.output.mkdir()
    (args.output/'packets.json').write_text(json.dumps(packets, ensure_ascii=False, indent=2)+'\n')
    (args.output/'requests.jsonl').write_text(''.join(json.dumps({'query_id': p['query_id'], 'messages': formatter(p)}, ensure_ascii=False)+'\n' for p in packets))
    (args.output/'preparation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k != 'fingerprints'}, indent=2))


if __name__ == '__main__':
    main()
