"""Serial offline queue; retrieval completes before fault and chain pressure load."""
from pathlib import Path
import argparse
import json
import subprocess
import sys
import time

OUT=Path('/evidence/formal_20261019')

def run(command):
    print(json.dumps({'queue_start':command,'monotonic_ns':time.perf_counter_ns()}),flush=True)
    result=subprocess.run(command)
    print(json.dumps({'queue_end':command,'returncode':result.returncode}),flush=True)
    return result.returncode

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--continue-pressure',action='store_true',help='Only use after real-model timing phases have finished.')
    args=parser.parse_args()
    while not (OUT/'analysis/retrieval_matrix_process_status.json').exists():time.sleep(1)
    run([sys.executable,'/drivers/formal_retrieval_analyze.py'])
    if (OUT/'analysis/STOP_OFFLINE_AFTER_CELL').exists():return
    faults=('router_crash','drop_ack','corrupt_state','timeout','duplicate_message','wrong_memory')
    if not all((OUT/'analysis'/f'stress_fault_{fault}_status.json').exists() for fault in faults):
        run([str(v) for v in [sys.executable,'/drivers/formal_monitor.py','stress_faults_v1',4200,
            sys.executable,'/drivers/formal_stress_faults.py']])
    if not args.continue_pressure:
        print('Retrieval and faults completed; pressure deferred intact until real-model timing phases finish.',flush=True)
        run([sys.executable,'/drivers/formal_stress_analyze.py'])
        return
    if (OUT/'analysis/STOP_OFFLINE_AFTER_CELL').exists():return
    run([str(v) for v in [sys.executable,'/drivers/formal_monitor.py','stress_matrix_v3',54000,
        sys.executable,'/drivers/formal_stress_matrix.py']])
    run([sys.executable,'/drivers/formal_stress_analyze.py'])

if __name__=='__main__':main()
