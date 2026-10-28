"""Offline format-fidelity checks. Run after real-provider timing; no API/raw study reads."""
from pathlib import Path
import csv
import json
import tempfile
import unittest
from unittest.mock import patch

import pyarrow.parquet as pq
import formal_export as export


class ExactExports(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='formal-export-contract-')
        self.addCleanup(self.temporary.cleanup)
        self.base=Path(self.temporary.name)
        (self.base/'raw').mkdir()
        self.destination=self.base/'exports';self.destination.mkdir()

    def write(self,rows):
        path=self.base/'raw/task_runs.jsonl'
        path.write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in rows),encoding='utf-8')
        return path

    def test_missing_null_empty_unknown_integer_float_and_nested_roundtrip(self):
        rows=[{'run_id':'A','optional':None,'blank':'','status':'UNKNOWN','mixed_numeric':1,
               'large':2**70,'nested':{'x':[None,'',False,0,{'y':'中文,\n"quoted"'}]},'typed_count':0},
              {'run_id':'B','blank':None,'status':None,'mixed_numeric':1.0,'large':1.25,
               'nested':[],'typed_count':2},
              {'run_id':'C','optional':'null','status':'0','mixed_numeric':-0.0,
               'large':-(2**80),'typed_count':None}]
        path=self.write(rows);before=export.hash_file(path)
        with patch.object(export,'OUT',self.base):result=export.export_table('task_runs',self.destination)
        self.assertEqual(result['status'],'PASS');self.assertEqual(result['rows'],3)
        self.assertEqual(before,export.hash_file(path))
        storage=json.loads((self.destination/'task_runs.storage.json').read_text())
        self.assertIn('large',storage['canonical_json_parquet_columns'])
        self.assertIn('mixed_numeric',storage['canonical_json_parquet_columns'])
        with (self.destination/'task_runs.csv').open(newline='',encoding='utf-8-sig') as stream:
            saved=list(csv.DictReader(stream))
        reconstructed=[]
        for item in saved:
            keys=json.loads(item[export.PRESENT])
            reconstructed.append({key:json.loads(item[key]) for key in keys})
        self.assertEqual([export.canonical(r) for r in reconstructed],[export.canonical(r) for r in rows])
        self.assertNotIn('optional',reconstructed[1]);self.assertIsNone(reconstructed[0]['optional'])
        self.assertEqual(reconstructed[0]['blank'],'');self.assertEqual(reconstructed[2]['optional'],'null')
        self.assertIs(type(reconstructed[0]['mixed_numeric']),int)
        self.assertIs(type(reconstructed[1]['mixed_numeric']),float)
        column=pq.read_table(self.destination/'task_runs.parquet').to_pylist()
        self.assertEqual(column[0]['typed_count'],0);self.assertIsNone(column[2]['typed_count'])
        schema=json.loads((self.destination/'task_runs.schema.json').read_text())
        self.assertEqual(schema['$schema'],'https://json-schema.org/draft/2020-12/schema')
        self.assertIn('run_id',schema['required']);self.assertNotIn('optional',schema['required'])
        self.assertFalse(schema['additionalProperties'])

    def test_empty_required_table_is_incomplete_not_invalid_schema(self):
        self.write([])
        with patch.object(export,'OUT',self.base):result=export.export_table('task_runs',self.destination)
        self.assertEqual(result['status'],'INCOMPLETE')
        self.assertFalse((self.destination/'task_runs.schema.json').exists())

    def test_large_csv_cell_has_independent_readback(self):
        self.write([{'run_id':'long-cell','data':'中'*(140000),'nullable':None}])
        with patch.object(export,'OUT',self.base):result=export.export_table('task_runs',self.destination)
        self.assertEqual(result['readback']['status'],'PASS')
        self.assertEqual(result['readback']['csv_reconstructed_sha256'],result['readback']['source_decoded_sha256'])

    def test_existing_exports_are_not_overwritten(self):
        self.write([{'run_id':'one','success':False}])
        with patch.object(export,'OUT',self.base):
            export.export_table('task_runs',self.destination)
            with self.assertRaises(RuntimeError):export.export_table('task_runs',self.destination)

    def test_nonfinite_literals_including_nested_are_rejected(self):
        for value in [float('nan'),float('inf'),-float('inf'),{'nested':[float('nan')]}]:
            with self.subTest(value_type=type(value).__name__):
                path=self.write([{'run_id':'not-standard-json','value':value}])
                before=export.hash_file(path)
                with patch.object(export,'OUT',self.base):
                    with self.assertRaises(RuntimeError):export.export_table('task_runs',self.destination)
                self.assertEqual(export.hash_file(path),before)
                self.assertFalse((self.destination/'task_runs.parquet').exists())
        with self.assertRaises(ValueError):export.canonical({'nested':[float('inf')]})


if __name__=='__main__':unittest.main(verbosity=2)
