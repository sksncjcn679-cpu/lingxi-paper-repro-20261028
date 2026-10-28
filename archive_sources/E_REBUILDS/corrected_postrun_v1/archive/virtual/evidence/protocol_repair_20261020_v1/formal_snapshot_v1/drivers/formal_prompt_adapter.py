"""Protocol fact-equivalence guard: keep native representations, avoid duplicate facts, resolve missing references."""
import json


def model_prompt(prompt,expected_observations):
    if '\nObservations: ' not in prompt or not prompt.startswith('Inbound message:\n'):
        raise ValueError('formal main requires native inbound representation plus verifiable source facts')
    prefix,serialized=prompt.rsplit('\nObservations: ',1)
    observations,end=json.JSONDecoder().raw_decode(serialized)
    if observations!=expected_observations or serialized[end:].strip():
        raise ValueError('source observations mismatch or unexpected trailing prompt content')
    wire,task=prefix[len('Inbound message:\n'):].split('\n\nTask: ',1)
    instructions,procedure=task.rsplit('\nProcedure: ',1)
    if wire.lstrip().startswith('{'):
        frame=json.loads(wire)
        params=frame.get('ip',frame.get('input_parameters',{}))
        view='native_structured'
    else:
        if ' | input=' not in wire:raise ValueError('lean full-payload input field is absent')
        params,_=json.JSONDecoder().raw_decode(wire.split(' | input=',1)[1])
        view='native_lean_text'
    evidence=params.get('evidence',{}) if isinstance(params,dict) else {}
    inline=evidence.get('observations') if isinstance(evidence,dict) else None
    if inline is not None:
        if inline!=observations:raise ValueError('inbound inline facts disagree with resolved source facts')
        result='Inbound message:\n'+wire+'\n\nTask: '+instructions
        if evidence.get('procedure')!=procedure:
            result+='\nAdopted procedure: '+procedure
        source='inline_native_wire'
    else:
        if not isinstance(params,dict) or not any(k in params for k in ['evidence_ref','facts_ref']):
            raise ValueError('neither inline facts nor a resolvable evidence reference is present')
        result='Inbound message:\n'+wire+'\n\nTask: '+instructions+'\nProcedure: '+procedure
        result+='\nResolved reference observations: '+json.dumps(observations,ensure_ascii=False,separators=(',',':'),sort_keys=True)
        source='actual_reference_resolved_observations'
    return result,{'representation':view,'facts_source':source,'source_facts_equal':True,
        'expected_fact_inventory':sorted(observations),'inline_observation_copies':int(inline is not None),
        'resolved_observation_copies':int(inline is None),'duplicate_task_fact_section_removed':True,
        'instruction_contract_unchanged':True,'adopted_procedure':procedure}
