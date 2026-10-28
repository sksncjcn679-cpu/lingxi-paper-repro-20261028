"""Meaningful protocol regression guards, including the original fact-duplication failure."""
import json
import unittest
from formal_prompt_adapter import model_prompt


class PromptGuardTests(unittest.TestCase):
    def setUp(self):
        self.obs={'incident_id':'x','current_evidence':'required SONAME missing','checks':{'rebuild_binary':'fixture-check'}}
        self.evidence={'observations':self.obs,'procedure':'inspect current evidence'}
    def prompt(self,wire):
        return 'Inbound message:\n'+wire+'\n\nTask: Choose diagnosis and action.\nProcedure: inspect current evidence\nObservations: '+json.dumps(self.obs)
    def test_text_and_structured_have_same_facts_once_and_different_representations(self):
        wires=['from=retriever | to=executor | input='+json.dumps({'evidence':self.evidence}),
               json.dumps({'ip':{'evidence':self.evidence}})]
        outputs=[]
        for wire in wires:
            value,audit=model_prompt(self.prompt(wire),self.obs)
            self.assertEqual(value.count('required SONAME missing'),1)
            self.assertEqual(audit['inline_observation_copies']+audit['resolved_observation_copies'],1)
            outputs.append(value)
        self.assertNotEqual(*outputs)
    def test_reference_group_gets_complete_observations_once(self):
        value,audit=model_prompt(self.prompt(json.dumps({'ip':{'evidence_ref':'artifact://x'}})),self.obs)
        self.assertEqual(value.count('required SONAME missing'),1)
        self.assertEqual(audit['facts_source'],'actual_reference_resolved_observations')
    def test_inconsistent_inline_facts_fail_closed(self):
        with self.assertRaises(ValueError):
            model_prompt(self.prompt(json.dumps({'ip':{'evidence':{'observations':{'wrong':True}}}})),self.obs)
    def test_missing_reference_or_missing_native_view_is_rejected(self):
        with self.assertRaises(ValueError):model_prompt(self.prompt('{}'),self.obs)
        with self.assertRaises(ValueError):model_prompt('Task only\nObservations: '+json.dumps(self.obs),self.obs)


if __name__=='__main__':unittest.main()
