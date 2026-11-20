"""Read-only, whole-run elapsed-time benchmark for the JCEM full baseline checker.

Example (from PyCharm project folder):
  python benchmark_full_pipeline.py --models "data/fixtures/stair_mep_mutation_base.ifc" --repetitions 3 --out full_pipeline_benchmark.json

Times full run_ifc_baseline.py in a fresh subprocess. This includes Python
startup and all implementation work done by that script, but excludes this
benchmark controller's input checks and parsing of the resulting JSON.
This is NOT a partial-vs-full end-to-end speed comparison.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time


def sha256_file(p):
    digest = hashlib.sha256()
    with p.open('rb') as fh:
        for chunk in iter(lambda: fh.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def stable_outcome(model):
    return {
        'component_counts': model.get('counts'),
        'rdf_triples': model.get('rdf_triples'),
        'procedural_totals': model.get('procedural', {}).get('totals'),
        'sparql_totals': model.get('sparql', {}).get('totals'),
        'shacl_totals': model.get('shacl', {}).get('totals'),
        # Deliberately do not rely on the existing runner's method_disagreements
        # (its mixed PASS/FAIL model-level aggregation can be incorrect).
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', nargs='+', required=True, help='Input .ifc models')
    parser.add_argument('--repetitions', type=int, default=3)
    parser.add_argument('--out', default='full_pipeline_benchmark.json')
    parser.add_argument('--runner', default=str(Path(__file__).resolve().with_name('run_ifc_baseline.py')))
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 50:
        parser.error('--repetitions must be between 1 and 50')
    runner = Path(args.runner).resolve()
    if not runner.is_file():
        parser.error('Cannot find baseline runner: ' + str(runner))
    models = [Path(s).resolve() for s in args.models]
    missing = [str(m) for m in models if not m.is_file()]
    if missing:
        parser.error('Cannot find input model(s): ' + ', '.join(missing))
    destination = Path(args.out).resolve()
    if destination in models:
        parser.error('Output file cannot replace an input IFC model')

    output = {
        'status': 'DIAGNOSTIC_FULL_PIPELINE_WALL_CLOCK_NOT_EXPERT_VALIDATED',
        'timestamp_utc': datetime.now(timezone.utc).isoformat(),
        'python_executable': sys.executable,
        'python_version': sys.version,
        'platform': platform.platform(),
        'runner_name': runner.name,
        'runner_sha256': sha256_file(runner),
        'repetitions_requested_per_model': args.repetitions,
        'timing_scope': (
            'Wall-clock duration of an entire run_ifc_baseline.py child process: '
            'startup, IFC parsing, procedural checker, RDF generation, SPARQL and SHACL '
            'checks, and JSON writing. Excludes parent-side file preflight and result validation.'
        ),
        'limitations': [
            'Not an end-to-end partial-vs-full speedup; only the complete baseline was timed.',
            'No memory usage measured; no hardware controls or independent ground truth.',
            'Full checker has a known mixed PASS/FAIL roll-up issue; raw outcomes are retained.',
            'Large IFC contains conflicting native attribute/Pset values (factor 304.8), despite declared mm project units.',
            'Regulatory applicability of the tested rules has not been independently validated.',
        ],
        'models': [],
    }

    for source in models:
        print('Benchmarking:', source.name, flush=True)
        record = {
            'file_name': source.name,
            'bytes': source.stat().st_size,
            'sha256': sha256_file(source),
            'runs': [],
        }
        fingerprints = []
        with tempfile.TemporaryDirectory(prefix='jcem_full_') as directory:
            for rep in range(1, args.repetitions + 1):
                result_path = Path(directory) / ('run_%02d.json' % rep)
                cmd = [sys.executable, str(runner), '--models', str(source), '--out', str(result_path)]
                start = time.perf_counter()
                try:
                    proc = subprocess.run(cmd, cwd=str(runner.parent), capture_output=True,
                                          text=True, errors='replace', timeout=900)
                    duration = time.perf_counter() - start
                    result = {'repetition': rep, 'elapsed_seconds': duration,
                              'return_code': proc.returncode}
                    if proc.returncode:
                        result['error'] = 'Runner exited with nonzero code'
                        result['stderr_tail'] = (proc.stderr or '')[-3000:]
                    elif not result_path.is_file():
                        result['error'] = 'Runner did not produce a JSON report'
                        result['stdout_tail'] = (proc.stdout or '')[-1000:]
                    else:
                        try:
                            data = json.loads(result_path.read_text(encoding='utf-8'))
                            entries = data.get('models', [])
                            if len(entries) != 1 or entries[0].get('error'):
                                result['error'] = 'Runner JSON reports an unsuccessful model check'
                                result['runner_message'] = entries[0].get('error') if entries else 'No models'
                            else:
                                fingerprint = stable_outcome(entries[0])
                                fingerprints.append(fingerprint)
                                result['outcome'] = fingerprint
                        except (OSError, ValueError, TypeError, KeyError) as exc:
                            result['error'] = 'JSON result could not be validated: ' + str(exc)
                except subprocess.TimeoutExpired:
                    result = {'repetition': rep, 'error': 'Timed out after 900 seconds'}
                record['runs'].append(result)
                if 'elapsed_seconds' in result:
                    print('  Run %d/%d: %.3f s%s' % (
                        rep, args.repetitions, result['elapsed_seconds'],
                        ' [ERROR]' if result.get('error') else ''), flush=True)
                else:
                    print('  Run %d/%d: %s' % (rep, args.repetitions, result.get('error')), flush=True)

        successful = [r['elapsed_seconds'] for r in record['runs'] if not r.get('error')]
        record['n_successful'] = len(successful)
        record['n_errors'] = args.repetitions - len(successful)
        record['outcomes_consistent_across_successes'] = (
            all(f == fingerprints[0] for f in fingerprints) if fingerprints else None
        )
        if successful:
            record['mean_seconds'] = statistics.mean(successful)
            record['median_seconds'] = statistics.median(successful)
            record['std_seconds'] = statistics.stdev(successful) if len(successful) > 1 else 0.0
            record['min_seconds'] = min(successful)
            record['max_seconds'] = max(successful)
        output['models'].append(record)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding='utf-8')
    print('Saved:', destination, flush=True)


if __name__ == '__main__':
    main()
