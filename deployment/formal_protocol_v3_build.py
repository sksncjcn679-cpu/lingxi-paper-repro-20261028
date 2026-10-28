"""Preserve v2; generate v3 with a fresh producer process for each complete batch."""
from pathlib import Path

OUT = Path('/evidence/formal_20261019')
source = (OUT / 'drivers/formal_protocol_matrix_v2.py').read_text()
source = source.replace("import resource\n", "import resource\nimport subprocess\nimport argparse\n")
source = source.replace("raw/protocol_v2_batch_", "raw/protocol_v3_batch_")
source = source.split("if __name__=='__main__':", 1)[0]
source += '''if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch', type=int, choices=[1, 2, 3])
    args = parser.parse_args()
    if args.batch:
        count = batch(args.batch)
        result = {'status': 'COMPLETED', 'batch': args.batch, 'measured_rows': count,
                  'producer_pid': os.getpid(), 'producer_ppid': os.getppid()}
        path = OUT / f'analysis/protocol_v3_batch_{args.batch}.json'
        with path.open('x') as f:
            json.dump(result, f, indent=2)
        print(json.dumps(result), flush=True)
    else:
        plan = {'sizes': SIZES, 'codecs': CODECS, 'warmup': 100, 'repeats': 1000,
                'independent_process_batches': 3, 'planned_measured_rows': 60000,
                'producer_isolation': 'three fresh Python subprocesses; one independent spawned consumer each',
                'model_calls': 0, 'latency_scope': 'encode + AF_UNIX send/receive + decode, integrity hash and ACK',
                'native_lean_decoder': 'extracts complete business input JSON; full header roundtrip not certified',
                'failure_policy': 'exclusive creation; retain partial raw; never silently overwrite or restart',
                'excluded_socket_bytes': 'termination sentinel; no handshake is used; row bytes include measurement frame and ACK',
                'cpu_scope': 'producer per measurement; consumer decode and integrity verification per measurement'}
        with (OUT / 'analysis/protocol_v3_plan.json').open('x') as f:
            json.dump(plan, f, indent=2)
        results = []
        try:
            for index in range(1, 4):
                subprocess.run([sys.executable, __file__, '--batch', str(index)], check=True)
                results.append(json.loads((OUT / f'analysis/protocol_v3_batch_{index}.json').read_text()))
            pids = [r['producer_pid'] for r in results]
            if len(set(pids)) != 3:
                raise AssertionError('producer PID isolation failed')
            status = {'status': 'COMPLETED', 'planned_rows': 60000, 'measured_rows': sum(r['measured_rows'] for r in results),
                      'independent_producer_pids': pids, 'batches': results}
        except Exception as exc:
            status = {'status': 'FAILED', 'error': str(exc), 'traceback': traceback.format_exc(), 'batches': results}
            raise
        finally:
            with (OUT / 'analysis/protocol_v3_status.json').open('x') as f:
                json.dump(status, f, indent=2)
'''
target = OUT / 'drivers/formal_protocol_v3_matrix.py'
with target.open('x') as f:
    f.write(source)
print(target)
