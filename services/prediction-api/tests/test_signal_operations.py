import contextlib
import csv
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import signal_client
import signal_list
import signal_stress


class OperationsTest(unittest.TestCase):
    def test_export_can_use_saved_admin_key(self):
        with patch.dict(os.environ, {'SIGNAL_API_KEY':'admin-key-'*6}, clear=True), \
                patch.object(signal_client,'api_request',return_value={'rows':1}) as call, \
                contextlib.redirect_stdout(io.StringIO()):
            signal_client.main(['export'])
        self.assertEqual(call.call_args.args[:3],('POST','/signals/export','admin-key-'*6))

    def test_clear_live_uses_saved_admin_key(self):
        with patch.dict(os.environ, {'SIGNAL_API_KEY':'admin-key-'*6}, clear=True), \
                patch.object(signal_client,'api_request',return_value={'cleared':2}) as call, \
                contextlib.redirect_stdout(io.StringIO()):
            signal_client.main(['clear-live'])
        self.assertEqual(call.call_args.args[:3],('POST','/signals/clear','admin-key-'*6))

    def test_ledger_lists_latest_state_across_owners(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.csv"
            fields = ["signal_id", "owner", "destination", "status", "revision"]
            with path.open("w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                for ident, owner, status, rev in [('a','alice','pending',1),('b','bob','pending',1),('a','alice','deleted',2)]:
                    writer.writerow(dict(zip(fields,[ident,owner,'LIVE',status,rev])))
            self.assertEqual([r['signal_id'] for r in signal_list.read_signals(path,'LIVE','pending')],['b'])
            self.assertEqual(len(signal_list.read_signals(path)),2)

    def test_export_uses_explicit_key_and_post(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'exporter.key'
            path.write_text('export-key-'*6)
            path.chmod(0o600)
            with patch.dict(os.environ, {'SIGNAL_API_KEY':'sender-key-'*6}), \
                    patch.object(signal_client,'api_request',return_value={'rows':1}) as call, \
                    contextlib.redirect_stdout(io.StringIO()):
                signal_client.main(['export','--key-file',str(path)])
            self.assertEqual(call.call_args.args[:3],('POST','/signals/export','export-key-'*6))

    def test_sender_table_filters_and_handles_empty(self):
        records = [{'signal':{'signal_id':'a','destination':'LIVE'},'status':'pending','revision':1},
                   {'signal':{'signal_id':'b','destination':'PAPER'},'status':'queued','revision':1}]
        with patch.object(signal_client,'credential',return_value='test'), \
                patch.object(signal_client,'api_request',return_value=records), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            signal_client.main(['list','--table','--destination','LIVE','--status','pending'])
        self.assertIn('1 signal(s)',output.getvalue())
        self.assertNotIn('PAPER',output.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as output:
            signal_client.print_table([])
        self.assertIn('0 signal(s)',output.getvalue())

    def test_cleanup_never_selects_other_runs_or_final_signals(self):
        records = [{'signal':{'signal_id':ident},'status':status,'reason':None}
                   for ident,status in [('run-1','pending'),('other-1','pending'),('run-2','exported'),('run-3','deleted')]]
        self.assertEqual([r['signal']['signal_id'] for r in signal_stress.owned_test_records(records,'run-')],['run-1'])


if __name__ == '__main__':
    unittest.main()
