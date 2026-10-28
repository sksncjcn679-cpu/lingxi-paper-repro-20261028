"""Synthetic-only regression fixtures for the independent external auditor.

Creates new temporary directories; never reads the formal evidence directory,
loads credentials, calls a model or imports a framework. Prepared during the real
matrix; execute only after the root schedules an idle verification window.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import formal_external_measurement_audit as auditor


def write_rows(path,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))


def make_fixture(root,first_mode='success',cache='missing'):
    """A complete 240-unit synthetic recorder snapshot; not experiment evidence."""
    root=Path(root);raw=root/'raw';raw.mkdir(parents=True)
    corpus=[]
    for domain in ('service','migration','resources','network_permissions'):
        label={'service':'port_conflict','migration':'compatible','resources':'disk_full','network_permissions':'dns_failure'}[domain]
        cue={'service':'EADDRINUSE','migration':'LOAD_OK','resources':'ENOSPC','network_permissions':'SERVFAIL'}[domain]
        for chain in range(5):
            for checkpoint,difficulty in ((1,'basic'),(3,'ambiguous'),(5,'interference')):
                task=f'synthetic-{domain}-{chain}-{checkpoint}'
                row={'task_id':task,'chain_id':f'synthetic-{domain}-{chain}','domain':domain,'difficulty':difficulty,
                    'split':'formal','parent_ids':[],'state_spec':{'applicable':True,'size_class':'small'},
                    'procedure':'Inspect only current supplied fixture evidence.',
                    'observations':{'current_evidence':cue+' '+task,'checkpoint':checkpoint,
                        'checks':{action:f'fixture-check {task} {action}' for action in auditor.LABEL_ACTIONS.values()}},
                    'expected':{'diagnosis':label,'action':auditor.LABEL_ACTIONS[label]}}
                row['content_sha256']=auditor.recorded_sha(row);corpus.append(row)
    write_rows(root/'corpus/automatic_validated_v1/tasks.jsonl',corpus)
    cfg={'model':'qwen3.7-plus','temperature':0,'max_tokens':1500,'timeout_sec':60,
         'extra_body':{'enable_thinking':False},'reasoning_effort':None}
    (root/'manifests').mkdir()
    (root/'manifests/native_configurations.json').write_text(json.dumps({'full_SS1':{'llm':cfg}}))
    (root/'manifests/budget_limits.json').write_text(json.dumps({'max_actual_utf8_request_bytes':131072}))
    tables={name:[] for name in auditor.TABLES};plan=[]
    for framework in ('autogen','langgraph'):
        for repeat in (0,1):
            for fixture in corpus:
                identity=hashlib.sha256(f"synthetic/{framework}/{repeat}/{fixture['task_id']}".encode()).hexdigest()[:32]
                unit={'run_id':identity,'unit_id':identity,'phase':'external_'+framework,'task_id':fixture['task_id'],
                    'chain_id':fixture['chain_id'],'domain':fixture['domain'],'difficulty':fixture['difficulty'],
                    'arm':'T0','repeat':repeat,'model':'main','processes':False,'topology':'framework_inprocess','intervention':None}
                plan.append(unit)
    (root/'manifests/formal_plan.json').write_text(json.dumps(plan))
    source_sha=hashlib.sha256(b'synthetic-only-source-identity').hexdigest()
    frozen={}
    for name in ('formal_external_runner.py','formal_external_adapters.py'):
        source=root/'drivers'/name;source.parent.mkdir(exist_ok=True)
        source.write_text('# Synthetic unit-test source marker; not a deployed runtime.\n')
        frozen[str(source)]=auditor.recorded_sha(source.read_bytes())
    (root/'manifests/formal-freeze.json').write_text(json.dumps({'source_sha256':source_sha,'files':frozen,'scope':'SYNTHETIC_UNIT_TEST_NOT_MODEL_EVIDENCE'}))
    for number,unit in enumerate(plan):
        fixture=corpus[number%60];framework=auditor.PHASES[unit['phase']]
        mode=first_mode if number==0 else 'success'
        expected_plan,evidence,messages,fact=auditor._expected(fixture)
        text=json.dumps(fixture['expected']);errors=[]
        if mode=='wrong':
            wrong=next(label for label in auditor.LABEL_ACTIONS if label!=fixture['expected']['diagnosis'])
            text=json.dumps({'diagnosis':wrong,'action':auditor.LABEL_ACTIONS[wrong]})
        elif mode=='malformed':
            text='{bad-json';errors=[{'stage':'executor','type':'JSONDecodeError','message':'synthetic invalid JSON'}]
        elif mode=='http_error':
            text=None;errors=[{'stage':'executor','type':'BadRequestError','message':'synthetic non-transient request error'}]
        answer=None
        if text is not None:
            try:
                decision=auditor._decision(text)
                answer={**decision,'probe':fixture['observations']['checks'][decision['action']]}
            except ValueError:pass
        success=answer=={**fixture['expected'],'probe':fixture['observations']['checks'][fixture['expected']['action']]}
        verdict={'success':success,'quality':float(success),'detail':{'exact_contract_match':success}}
        tool=None
        if answer is not None:
            matched=answer['action']==fixture['expected']['action'];current=fixture['observations']['current_evidence']
            tool={'task_id':fixture['task_id'],'action':answer['action'],'current_record_matches_check':matched,
                'evidence':current if matched else None,'source_sha256':hashlib.sha256(current.encode()).hexdigest(),
                'read_only':True,'scope':'authored fixture evidence; not live host diagnosis'}
        outputs={'planner':{'plan':expected_plan},'retriever':{'evidence':evidence},
                 'executor':{'answer':answer,'execution_errors':errors},'reviewer':{'answer':answer,'verdict':verdict}}
        common={**unit,'model_id':'qwen3.7-plus','source_sha256':source_sha,'task_sha256':fixture['content_sha256'],
            'seed':20261019,'framework':framework,'actual_topology':'framework_inprocess','model_evidence':'real_provider'}
        tables['task_unit_starts'].append({**unit,'start_utc':'2026-10-19T00:00:00+00:00'})
        base=10_000_000_000+number*3_000_000_000;owner=91001
        role_intervals={'planner':(10_000_000,20_000_000),'retriever':(25_000_000,35_000_000),
            'executor':(40_000_000,1_700_000_000),'reviewer':(1_750_000_000,1_800_000_000)}
        n_attempts=3 if mode=='retry_success' else 1
        spans=[];role_span_ids={};next_id=0
        for role in auditor.ROLES:
            next_id+=1;role_span_ids[role]=next_id;a,b=role_intervals[role]
            span={'span_id':next_id,'parent_span_id':None,'category':{'planner':'planning','retriever':'retrieval','executor':'execution','reviewer':'verify'}[role],
                'operation':framework+'.'+role,'pid':owner,'thread':1,'depth':0,'start_ns':base+a,'end_ns':base+b,'inclusive_sec':(b-a)/1e9}
            if role=='executor':
                for attempt in range(n_attempts):
                    next_id+=1;a2=(45_000_000,600_000_000,1_200_000_000)[attempt];b2=a2+10_000_000
                    spans.append({'span_id':next_id,'parent_span_id':role_span_ids[role],'category':'provider_wait',
                        'operation':'chat_completions_create','pid':owner,'thread':1,'depth':1,
                        'start_ns':base+a2,'end_ns':base+b2,'inclusive_sec':.01,'request_id':unit['run_id']+f'-{attempt+1}'})
                    if attempt<n_attempts-1:
                        next_id+=1
                        spans.append({'span_id':next_id,'parent_span_id':role_span_ids[role],'category':'retry_backoff',
                            'operation':'bounded_transient_retry_wait','pid':owner,'thread':1,'depth':1,
                            'start_ns':base+b2,'end_ns':base+b2+500_000_000,'inclusive_sec':.5})
                        tables['provider_retry_waits'].append({**common,'request_id':unit['run_id']+f'-{attempt+1}',
                            'wait_sec':.5,'error_type':'InternalServerError'})
            spans.append(span)
        next_id+=1
        spans.append({'span_id':next_id,'parent_span_id':None,'category':'cleanup','operation':'close_external_provider_client',
            'pid':owner,'thread':1,'depth':0,'start_ns':base+1_850_000_000,'end_ns':base+1_900_000_000,'inclusive_sec':.05})
        for attempt in range(1,n_attempts+1):
            ok=mode!='http_error' and (mode!='retry_success' or attempt==3)
            usage=None if not ok or mode=='no_usage' else {'prompt_tokens':12,'completion_tokens':8,'total_tokens':20}
            if usage is not None and cache!='missing':usage['prompt_tokens_details']={'cached_tokens':cache}
            body={'model':'qwen3.7-plus','messages':messages,'temperature':0,'max_tokens':1500,'enable_thinking':False}
            encoded=json.dumps(body,ensure_ascii=False,separators=(',',':')).encode();request_id=unit['run_id']+f'-{attempt}'
            call={**common,'request_id':request_id,'attempt':attempt,'requested_model':'qwen3.7-plus',
                'endpoint':'https://www.dmxapi.cn/v1/chat/completions','actual_prompt_sha256':auditor.recorded_sha(messages),
                'prompt_sha256':auditor.recorded_sha(messages),'temperature':0,'max_tokens':1500,'timeout_sec':60,
                'extra_body':{'enable_thinking':False},'reasoning_effort':None,'sdk_max_retries':0,
                'fact_audit':[fact],'business_facts_equal':True,'status':'OK' if ok else 'ERROR',
                'usage_raw':usage,'usage_source':'provider_reported' if usage is not None else 'missing' if ok else 'none',
                'actual_http_dispatches':1,'actual_request_utf8_bytes':len(encoded),'actual_http_body_sha256':auditor.recorded_sha(encoded),
                'request_encoding':'actual_SDK_HTTP_request_content','http_transport_eligible':True,'wall_sec':.012,
                'idempotency':'client_headers_not_verified_server_deduplication'}
            start=deepcopy(call);start.update(actual_http_dispatches=0,actual_request_utf8_bytes=None,usage_raw=None,usage_source='none')
            tables['provider_attempt_starts'].append(start)
            tables['model_io'].extend([{**common,'request_id':request_id,'direction':'request','messages':messages},
                {**common,'request_id':request_id,'direction':'http_request_body','request':body,'actual_utf8_bytes':len(encoded),'headers':'NOT_RETAINED'}])
            if ok:
                response={'id':'synthetic-response','model':'synthetic-returned-model-alias',
                    'choices':[{'index':0,'message':{'role':'assistant','content':text},'finish_reason':'stop'}], 'usage':usage}
                call.update(returned_model=response['model'],finish_reason='stop',response_sha256=auditor.recorded_sha(response))
                tables['model_io'].append({**common,'request_id':request_id,'direction':'response','response':response})
            else:call.update(error_type='BadRequestError' if mode=='http_error' else 'InternalServerError',error='synthetic HTTP error')
            tables['provider_calls'].append(call)
        events=[];transcript=[{'source':'user','content':auditor.compact({'task_id':fixture['task_id'],
            'description':'Diagnose current checkpoint and select its read-only fixture evidence check.'}),'type':'TextMessage','id':'synthetic-user'}]
        graph_state={'task_id':fixture['task_id']};node_trace=[]
        def emit(kind,role,tick,**kwargs):
            events.append({'unit_id':unit['run_id'],'framework':framework,'kind':kind,'role':role,'monotonic_ns':base+tick,**kwargs})
        for index,role in enumerate(auditor.ROLES):
            a,b=role_intervals[role]
            delivered=transcript[:] if framework=='autogen' else dict(graph_state)
            extra={'message_count':len(delivered)} if framework=='autogen' else {}
            emit('framework.agent_input',role,a-100,serialized_bytes=len(auditor.compact(delivered).encode()),sha256=auditor.compact_sha(delivered),**extra)
            emit('role.start',role,a-10,index=index)
            if role=='retriever':
                for j,(operand,value) in enumerate(evidence.items()):emit('tool.lookup',role,a+100+j,operand=operand,value_sha256=auditor.compact_sha(value),read_only=True)
            if role=='executor':
                emit('model.invoke',role,a+100,messages_sha256=auditor.compact_sha(messages),fact_audit=fact)
                if tool:emit('tool.fixture_check',role,b-100,**tool)
                elif errors:emit('executor.failure',role,b-100,error_type=errors[0]['type'],message=errors[0]['message'])
            emit('role.end',role,b-10,elapsed_ns=b-a,output_sha256=auditor.compact_sha(outputs[role]),output_bytes=len(auditor.compact(outputs[role]).encode()))
            node_trace.append({'role':role,'input_sha256':auditor.compact_sha(graph_state),'output':outputs[role]})
            graph_state.update(outputs[role]);transcript.append({'source':role,'content':auditor.compact(outputs[role]),'type':'TextMessage','id':'synthetic-'+role})
        framework_data=({'transcript':transcript,'stop_reason':'Maximum turns reached','actual_scheduler':'autogen_agentchat.teams.RoundRobinGroupChat',
            'state_semantics':'in_process_role_context_and_autogen_broadcast_transcript'} if framework=='autogen' else
            {'graph_final_state':graph_state,'node_transcript':node_trace,'actual_scheduler':'langgraph.graph.StateGraph.compile().ainvoke',
             'state_semantics':'in_process_typed_state_merge_no_checkpointer'})
        framework_data['agent_isolation']='logical_roles_in_same_process'
        fr={**common,'role_order':list(auditor.ROLES),'logical_model_invocations':1,'adapter_version':'formal-external-four-role-v1',
            'framework_versions':auditor.VERSIONS,'model_messages':messages,'model_response_text':text,'fact_audit':fact,
            'answer':answer,'success':success,'quality':float(success),'verdict':verdict,'errors':errors,'fixture_check':tool,
            'events':events,'framework_data':framework_data,'wall_ns':1_790_000_000,
            'comparison_scope':'SYSTEM_LEVEL_REFERENCE','baseline_contract':'native_T0_advisory_disabled_no_state_no_memory',
            'state_exchange':'NOT_ENABLED_NATIVE_SS_MECHANISM_NOT_IMPLEMENTED_BY_EXTERNAL_FRAMEWORK',
            'memory':'DISABLED_MATCHED_TO_NATIVE_T0','ipc_boundary':'NO_INTERPROCESS_TRANSPORT_IN_EXTERNAL_ADAPTER',
            'local_serialized_handoff_bytes_are_not_ipc_bytes':True}
        tables['external_framework_runs'].append(fr)
        tables['external_execution_events'].extend({**common,**e} for e in events)
        tables['spans'].extend({**common,**s} for s in spans)
        for k in range(9):tables['resource_samples'].append({**common,'monotonic_ns':base+1_000_000+k*200_000_000,
            'sample_interval_sec':.2,'processes':[{'pid':owner,'role':'runtime','rss':100_000_000,'fds':3,'cpu_sec':1.+k*.001,'io':None}]})
        all_usage=mode not in ('http_error','retry_success','no_usage')
        terminal={**common,'success':success,'quality':float(success),'status':'COMPLETED' if success else 'FAILED','outputs':{'answer':answer},
            'error':errors or None,'provider_attempts':n_attempts,'provider_usage_complete':all_usage,
            'provider_input_tokens':12 if all_usage else None,'provider_output_tokens':8 if all_usage else None,'provider_total_tokens':20 if all_usage else None,
            'provider_cached_input_tokens':(0 if cache=='missing' else cache) if all_usage else None,
            'reported_token_lower_bound':0 if mode in ('http_error','no_usage') else 20,
            'role_order':list(auditor.ROLES),'external_adapter_version':'formal-external-four-role-v1','external_framework_versions':auditor.VERSIONS,
            'observed_fixture_check':tool,'fact_audit':fact,'state_consumed':False,'local_state_materialized_bytes':0,'logical_state_bytes':0,
            'memory_count_before':0,'memory_count_after':0,'comparison_scope':'SYSTEM_LEVEL_REFERENCE','observable_ipc_bytes':0,
            'ipc_accounting':{'observed_socket_bytes':0,'by_boundary':{},'successful_syscalls':0,'framing_consumption_conserved':True,
                'scope':'external_framework_in_process_role_handoffs_no_AF_UNIX_control_or_AgentHost_RPC','excluded':['HTTP/provider network']},
            'wall_start_monotonic_ns':base,'wall_end_monotonic_ns':base+2_000_000_000,'wall_sec':2.,
            'runtime_cpu_sec':.1,
            'exclusive_spans_sec':auditor._partition(spans,base,base+2_000_000_000),'provider_wait_sec':.01*n_attempts,
            'logical_role_pids':{r:owner for r in auditor.ROLES},'worker_pids':{},'workers_remaining':[],
            'shm_names_created_but_remaining':[],'sampled_peak_process_group_rss':100_000_000,'sampled_peak_process_group_fds':3,
            'fd_count_before':3,'fd_count_after':3,'fd_targets_before':{'0':'/dev/null','1':'pipe:[1]','2':'pipe:[1]'},
            'fd_targets_after':{'0':'/dev/null','1':'pipe:[1]','2':'pipe:[1]'}}
        tables['task_runs'].append(terminal)
    for table,rows in tables.items():write_rows(raw/(table+'.jsonl'),rows)
    return tables


class ExternalAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='synthetic-external-audit-')
        self.root=Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def fixture(self,**kwargs):return make_fixture(self.root,**kwargs)

    def mutate(self,table,fn):
        path=self.root/'raw'/(table+'.jsonl');rows=[json.loads(s) for s in path.read_text().splitlines()]
        fn(rows);write_rows(path,rows)

    def assert_code(self,result,code):
        self.assertEqual(result['status'],'FAIL',result.get('measurement_errors'))
        self.assertIn(code,{e['code'] for e in result['measurement_errors']})

    def test_complete_both_frameworks_cache_unknown(self):
        self.fixture();result=auditor.audit(self.root)
        self.assertEqual(result['status'],'PASS',result['measurement_errors'])
        self.assertEqual(result['terminal_units'],240)
        self.assertEqual(result['cache_authority_units'],{'unknown':240})
        self.assertTrue(all(r['authoritative_cached_input_tokens'] is None for r in result['unit_audits']))
        self.assertTrue(all(r['provider_queue_latency_sec'] is None for r in result['unit_audits']))

    def test_explicit_cache_zero_is_distinct_from_missing(self):
        self.fixture(cache=0);result=auditor.audit(self.root)
        self.assertEqual(result['status'],'PASS',result['measurement_errors'])
        self.assertEqual(result['cache_authority_units'],{'explicit_known':240})
        self.assertTrue(all(r['authoritative_cached_input_tokens']==0 for r in result['unit_audits']))

    def test_wrong_answer_is_quality_failure_not_measurement_failure(self):
        self.fixture(first_mode='wrong');result=auditor.audit(self.root)
        self.assertEqual(result['status'],'PASS',result['measurement_errors'])
        self.assertEqual(len(result['model_quality_failures_retained']),1)

    def test_malformed_answer_is_retained(self):
        self.fixture(first_mode='malformed');result=auditor.audit(self.root)
        self.assertEqual(result['status'],'PASS',result['measurement_errors'])
        self.assertEqual(len(result['model_quality_failures_retained']),1)

    def test_http_failure_usage_unknown_and_reviewer_retained(self):
        self.fixture(first_mode='http_error');result=auditor.audit(self.root)
        self.assertEqual(result['status'],'PASS',result['measurement_errors'])
        self.assertEqual(len(result['model_quality_failures_retained']),1)

    def test_success_with_missing_usage_stays_unknown(self):
        self.fixture(first_mode='no_usage');result=auditor.audit(self.root)
        self.assertEqual(result['status'],'PASS',result['measurement_errors'])
        self.assertEqual(sum(not r['provider_usage_complete'] for r in result['unit_audits']),1)

    def test_three_recorded_attempts_reconcile(self):
        self.fixture(first_mode='retry_success');result=auditor.audit(self.root)
        self.assertEqual(result['status'],'PASS',result['measurement_errors'])
        self.assertEqual(result['real_provider_attempts'],242)

    def test_non_external_rows_are_not_native_prompt_parsed(self):
        self.fixture()
        self.mutate('task_runs',lambda rows:rows.append({'run_id':'native-ignore','phase':'main','unexpected_native_shape':True}))
        result=auditor.audit(self.root)
        self.assertEqual(result['status'],'PASS',result['measurement_errors'])

    def test_actual_http_body_parameter_tamper(self):
        self.fixture();self.mutate('model_io',lambda rows:next(r for r in rows if r['direction']=='http_request_body')['request'].__setitem__('enable_thinking',True))
        self.assert_code(auditor.audit(self.root),'EXTERNAL_ACTUAL_HTTP_BODY_FIELDS_MISMATCH')

    def test_prompt_hash_tamper(self):
        self.fixture();self.mutate('provider_calls',lambda rows:rows[0].__setitem__('actual_prompt_sha256','0'*64))
        self.assert_code(auditor.audit(self.root),'EXTERNAL_FIXED_TEMPLATE_FACTS_OR_REQUEST_HASH_MISMATCH')

    def test_duplicate_terminal(self):
        self.fixture();self.mutate('task_runs',lambda rows:rows.append(deepcopy(rows[0])))
        self.assert_code(auditor.audit(self.root),'DUPLICATE_EXTERNAL_TERMINAL')

    def test_missing_attempt_end(self):
        self.fixture();self.mutate('provider_calls',lambda rows:rows.pop(0))
        self.assert_code(auditor.audit(self.root),'EXTERNAL_REQUEST_START_END_NOT_ONE_TO_ONE')

    def test_langgraph_state_transition_tamper(self):
        self.fixture();self.mutate('external_framework_runs',lambda rows:next(r for r in rows if r['framework']=='langgraph')['framework_data']['graph_final_state'].__setitem__('answer',None))
        self.assert_code(auditor.audit(self.root),'EXTERNAL_LANGGRAPH_FINAL_STATE')

    def test_autogen_scheduler_identity_tamper(self):
        self.fixture();self.mutate('external_framework_runs',lambda rows:rows[0]['framework_data'].__setitem__('actual_scheduler','manual_loop'))
        self.assert_code(auditor.audit(self.root),'EXTERNAL_AUTOGEN_ACTUAL_TEAM_TRANSCRIPT_MISSING')

    def test_span_partition_tamper(self):
        self.fixture();self.mutate('task_runs',lambda rows:rows[0]['exclusive_spans_sec'].__setitem__('provider_wait',999.))
        self.assert_code(auditor.audit(self.root),'EXTERNAL_EXCLUSIVE_PARTITION_NOT_CONSERVED')

    def test_IPC_HTTP_conflation_is_rejected(self):
        self.fixture();self.mutate('task_runs',lambda rows:rows[0].__setitem__('observable_ipc_bytes',500))
        self.assert_code(auditor.audit(self.root),'EXTERNAL_ZERO_IPC_BOUNDARY_MISMATCH')

    def test_socket_FD_leak_is_rejected(self):
        self.fixture();self.mutate('task_runs',lambda rows:rows[0]['fd_targets_after'].__setitem__('9','socket:[unreleased]'))
        self.assert_code(auditor.audit(self.root),'EXTERNAL_UNRELEASED_UNIT_FD')

    def test_resource_peak_tamper(self):
        self.fixture();self.mutate('task_runs',lambda rows:rows[0].__setitem__('sampled_peak_process_group_rss',123))
        self.assert_code(auditor.audit(self.root),'EXTERNAL_RESOURCE_PEAK_NOT_REPLAYABLE')

    def test_frozen_source_tamper(self):
        self.fixture();(self.root/'drivers/formal_external_runner.py').write_text('# altered synthetic source\n')
        self.assert_code(auditor.audit(self.root),'FROZEN_FILE_HASH_CHANGED')

    def test_empty_external_raw_is_incomplete(self):
        self.fixture()
        for table in auditor.TABLES:write_rows(self.root/'raw'/(table+'.jsonl'),[])
        result=auditor.audit(self.root)
        self.assertEqual(result['status'],'INCOMPLETE',result['measurement_errors'])
        self.assertEqual(len(result['incomplete']),240)


if __name__=='__main__':unittest.main()
