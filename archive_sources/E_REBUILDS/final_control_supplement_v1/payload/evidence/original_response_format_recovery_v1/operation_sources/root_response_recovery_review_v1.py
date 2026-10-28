"""Verify safe outputs and denominator conservation; do not re-read responses."""
import csv
import hashlib
import json
from pathlib import Path

BASE=Path('E:/codex_rebuilds/original_response_format_recovery_v1')
FLAGS=['frozen_normalized_json_decoder_accepted','decoded_is_object','required_diagnosis_action_keys_present','required_keys_exact','frozen_parser_accepted']
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text(encoding='utf-8'))

def main():
 identity=read(BASE/'exact_identity_receipt.json');r=read(BASE/'reference/analysis/results.json')
 for side in ['reference','independent_rebuild']:
  e=read(BASE/side/'execution_receipt.json');g=read(BASE/side/'guard_result.json')
  assert e['actual_exit_code']==g['exit_code']==g['denied_count']==0
 for f in identity['files']:
  assert sha(BASE/'reference/analysis'/f['path'])==sha(BASE/'independent_rebuild/analysis'/f['path'])==f['reference_sha256']
 groups=r['summary_by_phase_arm_model_topology'];coverage=r['coverage']
 assert sum(x['planned_task_denominator'] for x in groups)==coverage['planned_tasks']==6504
 assert sum(x['actual_attempt_denominator'] for x in groups)==coverage['actual_provider_attempts']==6578
 assert sum(x['actual_returned_response_row_denominator'] for x in groups)==coverage['actual_returned_response_rows']==6414
 with (BASE/'reference/analysis/response_format_flags.csv').open(encoding='utf-8',newline='') as f:
  rows=[{k:json.loads(v) for k,v in row.items()} for row in csv.DictReader(f)]
 assert len(rows)==len({x['request_id'] for x in rows})==6414
 assert not {'response','content','messages','headers','error','diagnosis','action'}.intersection(rows[0])
 for row in rows:
  assert type(row['raw_line']) is int and row['raw_line']>0
  assert len(row['raw_line_sha256'])==64 and len(row['returned_text_sha256'])==64
  assert all(type(row[x]) is bool or row[x] is None for x in FLAGS)
 totals={field:{label:sum(x.get(field) is value for x in rows) for label,value in [('true',True),('false',False),('unknown',None)]} for field in FLAGS}
 for field in FLAGS:
  assert sum(totals[field].values())==6414
  for x in groups:
   assert sum(x[field+'_response_'+a] for a in ['true','false','unknown'])==x['actual_returned_response_row_denominator']
   assert sum(x[field+'_unique_task_'+a] for a in ['true','false','unknown'])==x['planned_task_denominator']
  assert all(sum(x[field+'_response_'+a] for x in groups)==totals[field][a] for a in ['true','false','unknown'])
 main=[x for x in groups if x['phase']=='main'];public=[x for x in groups if x['phase']=='public_calibration']
 assert len(main)==6 and sum(x['planned_task_denominator'] for x in main)==2160
 assert sum(x['planned_task_denominator'] for x in public)==144
 assert sum(x['no_response_tasks'] for x in main)==70
 assert all(x['model_id']=='qwen3.7-plus' and x['processes'] is False for x in main)
 review={'status':'ROOT_ACTUAL_FORMAT_RECOVERY_OUTPUT_AND_DENOMINATOR_REVIEW_COMPLETE',
  'identity_receipt_sha256':sha(BASE/'exact_identity_receipt.json'),'result_sha256':sha(BASE/'reference/analysis/results.json'),
  'actual_two_exit_codes':[0,0],'guard_denied_counts':[0,0],'coverage':coverage,
  'all_returned_response_flag_counts':totals,'all_response_CSV_metadata_only_and_6414_unique_request_ids':True,
  'all_group_response_and_task_true_false_unknown_conservation':True,
  'main':{'planned_task_units':2160,'returned_unique_responses':sum(x['actual_returned_response_row_denominator'] for x in main),'no_response_tasks_unknown_format':70,'model':'qwen3.7-plus'},
  'public_144_separated':True,'original_provider_or_model_io_response_bodies_reopened_by_this_review':False,
  'new_quality_scoring_CI_p_Holm_or_family':False,'primary_or_B8_241_identity_changed':False,
  'limits':'Safe CSV and deterministic result review only; executed adapter supplied the actual bound-stream reads. Format flags do not certify diagnosis accuracy, open explanation semantics or strict RFC JSON.'}
 with (BASE/'root_review_v1.json').open('x',encoding='utf-8') as f:
  json.dump(review,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
 print(json.dumps({'reviewed':True,'all_returned_response_flag_counts':totals,'root_review_sha256':sha(BASE/'root_review_v1.json')}))

if __name__=='__main__':main()
