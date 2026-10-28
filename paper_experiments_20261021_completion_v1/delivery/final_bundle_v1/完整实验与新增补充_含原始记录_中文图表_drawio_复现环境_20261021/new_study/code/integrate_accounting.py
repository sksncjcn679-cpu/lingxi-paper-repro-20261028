"""Bounded source transformation of the new driver copy only."""
from pathlib import Path
import ast
import hashlib
import json
import shutil
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'code/native/real_driver.py'
def main():
    source=P.read_text(encoding='utf-8')
    before=hashlib.sha256(P.read_bytes()).hexdigest()
    replacements=[
      ("OUT = Path('/evidence/protocol_repair_20261020_v1')", "OUT = Path('/output/main_batch')"),
      ("CORPUS = OUT / 'corpus/candidate_v2/tasks.jsonl'", "CORPUS = OUT / 'corpus/tasks.jsonl'"),
      ("data_dir='/opt/lingxi/protocol-repair-data/'", "data_dir='/output/main_batch/runtime_data/'"),
      ("Path('/opt/lingxi/protocol-repair-memory')", "Path('/output/main_batch/memory')"),
      ("key=('protocol_repair_20261020_v1',unit['phase']", "key=('completion_main_20261021_v1',unit['phase']"),
      ("class BudgetStop(RuntimeError):", "from provider_accounting import ProviderAccounting, AccountingStop\n\nclass BudgetStop(RuntimeError):"),
      ("        self.attempts=len(read_rows('provider_attempt_starts.jsonl'))", "        self.accounting=ProviderAccounting()\n        self.attempts=len(read_rows('provider_attempt_starts.jsonl'))"),
      ("    def check(self):\n        if self.attempts", "    def check(self):\n        if self.accounting.halted or any(not r.get('usage_complete') for r in read_rows('provider_accounting.jsonl')):\n            raise BudgetStop('ACCOUNTING_HALTED_NO_NEW_PROVIDER_CALLS')\n        if self.attempts"),
      ("                create=ctx.llm._client.chat.completions.create", "                create=ctx.llm._client.chat.completions.create\n                accounting=budget_ledger().accounting\n                ctx.llm._client._client.event_hooks.setdefault('request',[]).append(accounting.request_hook)\n                ctx.llm._client._client.event_hooks.setdefault('response',[]).append(accounting.response_hook)"),
      ("                    calls.append(call)\n                    recorder.add('provider_attempt_starts.jsonl'", "                    accounting.begin(request_id,kwargs['model'])\n                    calls.append(call)\n                    recorder.add('provider_attempt_starts.jsonl'"),
      ("                        usage=response.usage", "                        account=accounting.finish_success(response)\n                        recorder.add('provider_accounting.jsonl',account,durable=True)\n                        call.update(response_id_headers=account['response_ID_headers'],\n                            body_completion_id=account['ChatCompletion_id'],accounting_usage_complete=account['usage_complete'])\n                        if not account['usage_complete']:\n                            raise BudgetStop('PROVIDER_USAGE_UNCONFIRMED')\n                        usage=response.usage"),
      ("                    except Exception as exc:\n                        call.update(status='ERROR',error_type=type(exc).__name__,error=str(exc)[:800])\n                        raise", "                    except Exception as exc:\n                        if accounting._active is not None:\n                            account=accounting.finish_error(exc)\n                            recorder.add('provider_accounting.jsonl',account,durable=True)\n                            call.update(response_id_headers=account['response_ID_headers'],\n                                api_status_error_request_id=account.get('APIStatusError_request_id'),\n                                usage_raw=account.get('usage_raw'),accounting_usage_complete=account['usage_complete'])\n                        call.update(status='ERROR',error_type=type(exc).__name__,error=str(exc)[:800])\n                        raise BudgetStop('PROVIDER_ERROR_RECORDED_NO_AUTOMATIC_RETRY') from exc"),
      ("            result=execute(unit,row,memory,dry=dry)", "            result=execute(unit,row,memory,dry=dry)\n            if not dry and budget_ledger().accounting.halted:\n                raise BudgetStop('ACCOUNTING_HALTED_AFTER_RECORDED_TERMINAL_TASK')")
    ]
    changes=[]
    for old,new in replacements:
        if source.count(old)!=1:raise RuntimeError('SOURCE_PATCH_TARGET_NOT_UNIQUE: '+old[:65])
        source=source.replace(old,new,1);changes.append({'old':old,'new':new})
    ast.parse(source)
    # Keep the exact copied preimage as independent provenance; only this new file is edited.
    preimage=ROOT/'code/native_real_driver.preimage.py'
    with preimage.open('xb') as f:f.write(P.read_bytes())
    P.write_text(source,encoding='utf-8',newline='\n')
    shutil.copyfile(ROOT/'code/provider_accounting.py',ROOT/'code/native/provider_accounting.py')
    receipt={'status':'NEW_COPY_PATCHED_ONLY','historical_driver_sha256':before,
        'new_driver_sha256':hashlib.sha256(P.read_bytes()).hexdigest(),'changes':changes,
        'business_logic_unchanged':'Task/prompt/roles/validators/representation/memory logic retained; instrumentation and output isolation only',
        'policy':'Unknown/error attempt durable recorded before hard stop; no new calls after latch; no zero fill'}
    with (ROOT/'manifests/native_patch_receipt.json').open('x',encoding='utf-8') as f:json.dump(receipt,f,ensure_ascii=False,indent=2)
    print(json.dumps({k:receipt[k] for k in ['status','historical_driver_sha256','new_driver_sha256']}))
if __name__=='__main__':main()
