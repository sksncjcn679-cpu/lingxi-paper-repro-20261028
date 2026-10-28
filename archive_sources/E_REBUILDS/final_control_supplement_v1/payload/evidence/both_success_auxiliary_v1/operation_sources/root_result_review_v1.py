"""Read-only review of executed supplemental results, not a new analysis."""
import hashlib
import json
from pathlib import Path

ROOT=Path('F:/codex/多agent系统架构-软件学报')
BASE=Path('E:/codex_rebuilds/both_success_auxiliary_v1')
NEW=ROOT/'paper_experiments_20261019_pilot/protocol_repair_20261020_v1'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text(encoding='utf-8'))

def main():
 result=read(BASE/'reference/analysis/results.json')
 primary=read(NEW/'analysis/stats/formal_primary_v1.json')
 identity=read(BASE/'exact_identity_receipt.json')
 checks=[]
 assert len(result['rows'])==25
 for i,c in enumerate(primary['strata'][0]['comparisons']):
  pair=c['baseline']+'_'+c['target']; support=result['selected_pair_support'][i]
  assert support['pair_id']==pair
  for link in support['all_selected_run_id_raw_links']:
   assert link['baseline']['success'] is True and link['target']['success'] is True
  for row in [x for x in result['rows'] if x['pair_id']==pair]:
   original=c['metrics'][row['metric']]
   assert row['all_planned_status']==original['status']
   for dest,src in [('baseline_mean','baseline_mean'),('target_mean','target_mean'),
      ('difference','paired_difference'),('relative_change','relative_change'),
      ('difference_ci95','difference_ci95'),('relative_change_ci95','relative_change_ci95')]:
    assert row['all_planned_'+dest]==original.get(src)
   if row['metric']!='provider_input_tokens':
    assert row['auxiliary']==original['both_success_auxiliary']
   else:
    assert original['status']=='INCOMPLETE'
    if support['unknown_token_both_success_pairs']:
     assert row['auxiliary']['status']=='INCOMPLETE_BOTH_SUCCESS_TOKEN_USAGE'
     assert all(row['auxiliary'][k] is None for k in ['baseline_mean','target_mean','difference','relative_change'])
   checks.append({'pair_id':pair,'metric':row['metric'],'saved_primary_fields_equal':True,
    'auxiliary_status':row['auxiliary']['status']})
 for run in ['reference','independent_rebuild']:
  receipt=read(BASE/run/'execution_receipt.json');guard=read(BASE/run/'guard_result.json')
  assert receipt['actual_exit_code']==guard['exit_code']==guard['denied_count']==0
 for f in identity['files']:
  assert sha(BASE/'reference/analysis'/f['path'])==sha(BASE/'independent_rebuild/analysis'/f['path'])==f['reference_sha256']
 for name in ['append_summary.md','feishu.xml']:
  text=(BASE/'reference/analysis'/name).read_text(encoding='utf-8')
  assert '\ufffd' not in text and '双方均成功' in text
 review={'status':'ROOT_EXECUTED_RESULT_AND_PRIMARY_PROJECTION_REVIEW_COMPLETE',
  'identity_receipt_sha256':sha(BASE/'exact_identity_receipt.json'),
  'result_sha256':sha(BASE/'reference/analysis/results.json'),
  'original_primary_sha256':sha(NEW/'analysis/stats/formal_primary_v1.json'),
  'actual_two_runs_exit_codes':[0,0],'guard_denied_counts':[0,0],
  'reviewed_primary_projection_rows':checks,
  'all_four_deliverables_rehashed_and_equal':True,'utf8_chinese_files_verified':True,
  'primary_input_tokens_all_five_still_INCOMPLETE':True,
  'new_token_auxiliary_complete_count':sum(r['metric']=='provider_input_tokens' and r['auxiliary']['status']=='EXPLORATORY_SELECTION_ON_POST_EXECUTION_SUCCESS' for r in result['rows']),
  'raw_or_provider_bodies_reopened':False,
  'within_original_B8_241_identity':False,
  'limits':'Independent peer receipt is separate; saved20 auxiliary dicts exact; historical raw lines are existing-auditor metadata locators, not rehashed here.'}
 with (BASE/'root_review_v1.json').open('x',encoding='utf-8') as f:
  json.dump(review,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
 print(json.dumps({'reviewed':True,'rows':len(checks),'token_auxiliary_with_numeric_value':review['new_token_auxiliary_complete_count'],'root_review_sha256':sha(BASE/'root_review_v1.json')}))

if __name__=='__main__':main()
