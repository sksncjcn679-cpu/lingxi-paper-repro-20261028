#!/usr/bin/env python3
"""Run the controlled remediation suites; real API requests require --real and env config."""
from pathlib import Path
import argparse, subprocess, sys, os
ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser(); p.add_argument('--real',action='store_true'); p.add_argument('--repeat',type=int,default=5)
    args=p.parse_args(); failures=[]
    commands=[['retrieval-expanded'],['candidate-workflow','--repeat',str(args.repeat)],
              ['contest-workload'],['decision-workload','--repeat',str(args.repeat)]]
    if sys.platform.startswith('linux'):
        commands.append(['decision-workload','--repeat',str(args.repeat),'--agent-processes'])
    if args.real:
        commands.append(['decision-workload','--real','--repeat',str(args.repeat),'--checkpoint','results/decision_real_checkpoint.jsonl'])
    for command in commands:
        rc=subprocess.call([sys.executable,str(ROOT/'scripts/rt.py'),*command],cwd=ROOT)
        if rc: failures.append({'command':command,'exit_code':rc})
    print({'failures':failures},flush=True)
    return int(bool(failures))

if __name__=='__main__': raise SystemExit(main())
