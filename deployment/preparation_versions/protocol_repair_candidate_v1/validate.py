"""Read-only candidate audit; oracle never receives the author's expected pair.

Checks full transitive ancestry, independent fact classification, all 24 actions,
zero/multiple-cause rejection and query labels from causal capability eligibility.
No provider or business runtime is imported.
"""
from __future__ import annotations
import argparse
import collections
import copy
import hashlib
import json
from pathlib import Path
import unicodedata
from contract import DOMAINS, LABEL_ACTIONS
from oracle import classify_facts, check


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def file_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def permit_access(a):
    a['open_errno']=None
    for entry in a['path_entries']:
        entry['owner_uid']=a['caller_uid'];entry['mode_octal']='0700'


def healthy(domain,observations):
    o=copy.deepcopy(observations);f=o['measurements']
    if domain=='service':
        f['socket']['bind_errno']=None
        permit_access(f['unix_access']);f['audit']['records']=[]
        f['allocation'].update(write_errno=None,available_blocks=100,free_inodes=100,quota_used_bytes=0)
        f['resolver'].update(rcode='NOERROR',answers=['10.99.0.1'],direct_ip_probe='success')
        f['memory'].update(exit_signal=None,oom_kill_after=f['memory']['oom_kill_before'])
    elif domain=='migration':
        f['python']['artifact_abi']=f['python']['target_abi']
        f['elf'].update(artifact_machine=f['elf']['target_machine'],artifact_class=f['elf']['target_class'])
        f['needed']['installed_sonames']=list(f['needed']['required_sonames'])
        f['versions']['exported_versions']=list(f['versions']['required_versions'])
        f['interpreter']['installed_paths']=[f['interpreter']['pt_interp']]
        f['search']['effective_search_directories']=[f['search']['provider_directory']]
    elif domain=='resources':
        f['blocks'].update(available_blocks=100,write_errno=None)
        f['inodes'].update(free_inodes=100,create_errno=None)
        f['quota'].update(used_bytes=0,write_errno=None)
        f['memcg']['oom_kill_after']=f['memcg']['oom_kill_before']
        f['cpu'].update(quota_usec=f['cpu']['period_usec']*f['cpu']['allocated_cpus'],nr_throttled_delta=0,throttled_usec_delta=0)
        f['io'].update(pending_requests=0,enforced_bps=None,enforced_iops=None)
    else:
        f['dns'].update(rcode='NOERROR',answers=[f['route']['destination']],direct_ip_probe='success')
        f['route']['entries']=[{'prefix':f['route']['destination']+'/32','metric':1,'type':'unicast',
                                'interface':f['route']['permitted_egress_interfaces'][0]}]
        f['filter']['verdict']='accept';f['tls'].update(verify_return_code=0,tcp_connected=True)
        permit_access(f['credential']);f['audit']['records']=[]
    return o


def add_independent_second_cause(domain, observations, first_action):
    o=copy.deepcopy(observations);f=o['measurements']
    # Select one of two disjoint fault kinds; this code never imports generation.
    if domain=='service':
        if first_action!='inspect_socket_ownership':
            f['socket'].update(bind_errno='EADDRINUSE',owner_pid=f['socket']['service_pid']+9000,owner_alive=True)
        else:f['resolver'].update(rcode='SERVFAIL',answers=[],direct_ip_probe='success')
    elif domain=='migration':
        if first_action!='inspect_python_binary_tags':f['python']['artifact_abi']='unsupported-full-abi'
        else:f['elf']['artifact_machine']='different-machine'
    elif domain=='resources':
        if first_action!='inspect_block_accounting':f['blocks'].update(available_blocks=0,write_errno='ENOSPC')
        else:f['inodes'].update(free_inodes=0,create_errno='ENOSPC')
    else:
        if first_action!='inspect_dns_transaction':f['dns'].update(rcode='SERVFAIL',answers=[],direct_ip_probe='success')
        else:f['tls'].update(verify_return_code=20,tcp_connected=True)
    return o


def assert_rejected(domain,observations,count):
    try:classify_facts(domain,observations)
    except ValueError as exc:
        if f'found {count}' not in str(exc):raise
        return
    raise AssertionError(f'{domain}: accepted a {count}-cause record')


def forbidden_fields(value,path='observations'):
    found=[]
    if isinstance(value,dict):
        for key,child in value.items():
            if key in {'expected','diagnosis','correct_answer','standard_answer','reference_evidence','acceptable_alternatives','scorer_version'}:
                found.append(path+'.'+key)
            found.extend(forbidden_fields(child,path+'.'+key))
    elif isinstance(value,list):
        for index,child in enumerate(value):found.extend(forbidden_fields(child,path+f'[{index}]'))
    return found


def audit(corpus):
    before={p.name:file_sha(p) for p in corpus.iterdir() if p.is_file()}
    tasks=[json.loads(s) for s in (corpus/'tasks.jsonl').read_text(encoding='utf-8').splitlines()]
    queries=[json.loads(s) for s in (corpus/'queries.jsonl').read_text(encoding='utf-8').splitlines()]
    memories=[json.loads(s) for s in (corpus/'retrieval_memory_records.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(tasks)==144 and len(queries)==240 and len(memories)==120
    task_map={r['task_id']:r for r in tasks};assert len(task_map)==144
    formal=[r for r in tasks if r['split']=='formal'];pilot=[r for r in tasks if r['split']=='pilot']
    assert len(formal)==120 and len(pilot)==24
    assert len({r['chain_id'] for r in formal})==20 and len({r['chain_id'] for r in tasks})==24
    ancestry_pairs=[];oracle_rows=[];command_checks=0;negative_checks=0
    for row in tasks:
        digest=row['content_sha256'];content={k:v for k,v in row.items() if k!='content_sha256'}
        assert hashlib.sha256(canonical(content).encode()).hexdigest()==digest
        assert not forbidden_fields(row['observations'])
        actual=classify_facts(row['domain'],row['observations'])
        assert actual==row['expected'],row['task_id']
        # Changing author labels must have no effect on executable check results.
        wrong=copy.deepcopy(row);wrong['expected']={'diagnosis':'untrusted-author-label','action':'untrusted-author-action'}
        for action in LABEL_ACTIONS.values():
            result=check(wrong,action);assert result['read_only']
            assert result['current_record_matches_check']==(action==actual['action'])
            assert action in row['observations']['checks']
            command_checks+=1
        assert_rejected(row['domain'],healthy(row['domain'],row['observations']),0)
        assert_rejected(row['domain'],add_independent_second_cause(row['domain'],row['observations'],actual['action']),2)
        negative_checks+=2
        oracle_rows.append({'task_id':row['task_id'],'classification':actual,'evidence_sha256':hashlib.sha256(canonical(row['observations']['measurements']).encode()).hexdigest()})
        seen=set();pending=list(row['parent_ids'])
        while pending:
            parent_id=pending.pop()
            assert parent_id!=row['task_id'] and parent_id not in seen,'cycle or duplicate ancestor'
            seen.add(parent_id);parent=task_map[parent_id]
            assert parent['chain_id']==row['chain_id'] and parent['split']==row['split']
            assert parent['observations']['checkpoint']<row['observations']['checkpoint']
            assert parent['expected']!=row['expected'],(row['task_id'],parent_id)
            ancestry_pairs.append([row['task_id'],parent_id]);pending.extend(parent['parent_ids'])
        assert len(seen)==row['observations']['checkpoint']-1
        if row['parent_ids']:
            parent=task_map[row['parent_ids'][0]]
            assert row['observations']['causal_context']['previous_record_sha256']==hashlib.sha256(canonical(parent['observations']['measurements']).encode()).hexdigest()
    assert len(ancestry_pairs)==360
    for chain in {r['chain_id'] for r in tasks}:
        rows=[r for r in tasks if r['chain_id']==chain]
        assert len(rows)==6 and len({r['expected']['diagnosis'] for r in rows})==6 and len({r['expected']['action'] for r in rows})==6
    for domain in DOMAINS:
        rows=[r for r in formal if r['domain']==domain]
        assert collections.Counter(r['difficulty'] for r in rows)=={'basic':10,'ambiguous':10,'interference':10}
        assert sum(r['state_spec']['applicable'] for r in rows)==27
        assert sum(q['domain']==domain for q in queries)==60
    states=collections.Counter(r['state_spec']['size_class'] for r in formal if r['state_spec']['applicable'])
    assert states=={'small':36,'medium':36,'large':36}
    text_keys=[unicodedata.normalize('NFKC',' '.join(q['text'].split())).casefold() for q in queries]
    assert len(set(text_keys))==240
    assert len({q['query_id'] for q in queries})==240
    for q in queries:
        assert q['source_task_id'] in {r['task_id'] for r in formal}
        source=task_map[q['source_task_id']]
        assert q['chain_id']==source['chain_id'] and q['checkpoint']==source['observations']['checkpoint']
        relevant=[m['memory_id'] for m in memories if m['chain_id']==q['chain_id'] and m['checkpoint']<q['checkpoint']
                  and m['trusted'] and m['validity']=='current_procedure_v1'
                  and set(q['required_capabilities'])<=set(m['capabilities']) and not m['contains_final_answer']]
        assert q['relevant_memory_ids']==relevant,q['query_id']
        assert q['no_answer']==(not relevant)
        assert q['content_sha256']==hashlib.sha256(canonical(q['text']).encode()).hexdigest()
    no_answer=sum(q['no_answer'] for q in queries);assert no_answer>=60
    assert {p.name:file_sha(p) for p in corpus.iterdir() if p.is_file()}==before
    return {'status':'PASS_AUTOMATIC_CANDIDATE_CORPUS_NOT_FROZEN','tasks':144,'formal_tasks':120,'public_tasks':24,
            'chains':24,'formal_chains':20,'ancestral_pairs_checked':360,'repeated_ancestor_answer_pairs':0,
            'per_domain_formal_difficulty_counts':{'basic':10,'ambiguous':10,'interference':10},
            'applicable_formal_states':108,'state_size_counts':dict(states),'independent_oracle_matches':len(oracle_rows),
            'read_only_action_checks':command_checks,'expected_field_poisoning_changes_results':False,
            'zero_and_two_cause_negative_checks':negative_checks,'queries':240,'unique_normalized_native_query_texts':240,
            'answerable_queries':240-no_answer,'no_answer_queries':no_answer,
            'query_labels':'independent causal/time/trust/capability eligibility; no human scores',
            'files_unchanged':True,'input_sha256':before,'paid_api_calls':0,'is_freeze':False,
            'remaining_gates':['candidate-pool construction and available relevant sets at all four scales',
                'actual native/external business-fact and output-contract calibration',
                'per-URI state and index-only instrumentation calibration',
                '24-task public six-arm real-model pilot, power/budget gate and prospective full-study freeze'],
            'oracle_classifications':oracle_rows}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--corpus',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path);args=parser.parse_args()
    if args.output.exists():raise RuntimeError('audit output already exists; never overwrite')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    try:result=audit(args.corpus)
    except Exception as exc:
        args.output.write_text(json.dumps({'status':'FAIL_RETAINED','error_type':type(exc).__name__,
                                         'error':str(exc),'paid_api_calls':0},indent=2)+'\n',encoding='utf-8')
        raise
    result['validator_sha256']=file_sha(Path(__file__));result['oracle_sha256']=file_sha(Path(__file__).with_name('oracle.py'))
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('oracle_classifications','input_sha256')},ensure_ascii=False))


if __name__=='__main__':main()
