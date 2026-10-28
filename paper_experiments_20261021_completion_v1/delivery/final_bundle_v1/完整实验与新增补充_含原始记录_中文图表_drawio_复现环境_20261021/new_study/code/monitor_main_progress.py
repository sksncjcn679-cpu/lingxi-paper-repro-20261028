"""Light host-side monitoring only; append progress, never touches experiment raw."""
from pathlib import Path
from datetime import datetime, timezone
import json
import time
ROOT=Path(__file__).resolve().parents[1]
def main():
    output=ROOT/'logs/main_progress.jsonl'
    if output.exists():raise RuntimeError('MONITOR_LOG_ALREADY_EXISTS_NO_DUPLICATE_MONITOR')
    with output.open('x',encoding='utf-8',buffering=1) as stream:
        while True:
            p=ROOT/'logs/main_v1/stdout.log'
            n=sum(1 for line in p.open(encoding='utf-8') if line.startswith('{"phase": "main"') and line.endswith('\n')) if p.exists() else 0
            receipt=ROOT/'logs/main_v1/execution_receipt.json'
            row={'utc':datetime.now(timezone.utc).isoformat(),'main_terminal':n,'main_planned':2160,
                 'main_process_terminal':receipt.exists(),'whole_goal_progress':'Use155-item evidence ledger; this counter is only main execution'}
            if receipt.exists():row['actual_exit_code']=json.loads(receipt.read_text(encoding='utf-8'))['actual_exit_code']
            stream.write(json.dumps(row,ensure_ascii=False)+'\n');stream.flush()
            print(json.dumps({k:v for k,v in row.items() if k!='whole_goal_progress'}),flush=True)
            if receipt.exists():break
            time.sleep(50)
if __name__=='__main__':main()
