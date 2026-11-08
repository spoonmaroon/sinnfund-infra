import concurrent.futures
import base64
import hashlib
import csv
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import signal_ledger as ledger
from fastapi import HTTPException


class LedgerTest(unittest.TestCase):
    def test_admin_alone_grants_both_scopes_and_revocation_is_immediate(self):
        token = 'fictional-admin-'*4
        path = self.root/'admins.htpasswd'
        digest = base64.b64encode(hashlib.sha1(token.encode()).digest()).decode()
        with patch.dict(os.environ, {f'SIGNAL_{scope.upper()}_KEYS_FILE':str(self.root/name)
                for scope,name in [('sender','senders.htpasswd'),('exporter','exporters.htpasswd'),('admin','admins.htpasswd')]}):
            path.write_text('alice:{SHA}'+digest+'\n')
            for scope in ['sender','exporter']:
                self.assertEqual(ledger.authenticate('Bearer '+token,scope),'alice')
            path.write_text('')
            for scope in ['sender','exporter']:
                with self.assertRaises(HTTPException) as error:
                    ledger.authenticate('Bearer '+token,scope)
                self.assertEqual(error.exception.status_code,401)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        env = patch.dict(os.environ, {'SIGNAL_DATA_DIR':str(self.root),
            'SIGNAL_LIVE_ACCOUNT':'TEST', 'SIGNAL_MAX_QUOTE_AGE_SECONDS':'300'})
        env.start()
        self.addCleanup(env.stop)
        ledger._ledger_cache = None

    def create(self, ident='one', price=110):
        return ledger.create(ledger.SignalCreate(signal_id=ident, destination='LIVE',
            ticker=ident.upper(), price=price, timestamp=123), owner='alice')

    def events(self):
        with (self.root/'ledger.csv').open(newline='') as f:
            return list(csv.DictReader(f))

    def test_lifecycle_preserves_history_and_file_prefix(self):
        self.create()
        original = (self.root/'ledger.csv').read_bytes()
        ledger.edit(ledger.SignalEdit(signal_id='one',revision=1,price=120),owner='alice')
        self.assertTrue((self.root/'ledger.csv').read_bytes().startswith(original))
        ledger.delete_signal(ledger.SignalDelete(signal_id='one',revision=2),owner='alice')
        record = ledger.get_signal('one',owner='alice')
        self.assertEqual([e['event'] for e in record['history']],['created','edited','deleted'])
        self.assertEqual(record['revision'],3)
        self.assertEqual(len(self.events()),3)
        ledger._ledger_cache = None
        self.assertEqual(ledger.get_signal('one',owner='alice'),record)

    def test_cache_does_not_reparse_unchanged_history(self):
        self.create()
        with patch.object(ledger.csv,'DictReader',side_effect=AssertionError('history reparsed')):
            self.assertEqual(ledger.get_signal('one',owner='alice')['status'],'pending')
            self.assertEqual(self.create()[0]['operation'],'unchanged')

    def test_failed_write_does_not_change_disk_or_cache(self):
        self.create()
        original = (self.root/'ledger.csv').read_bytes()
        with patch.object(ledger.os,'replace',side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                self.create('two')
        self.assertEqual((self.root/'ledger.csv').read_bytes(),original)
        self.assertEqual([r['signal']['signal_id'] for r in ledger.list_signals(owner='alice')],['one'])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),['.ledger.lock','ledger.csv'])
        self.assertEqual(self.create('two')[0]['operation'],'created')

    def test_failed_directory_sync_is_recovered_from_disk(self):
        self.create()
        with patch.object(ledger,'fsync_directory',side_effect=OSError('sync failure')):
            with self.assertRaises(OSError):
                self.create('two')
        self.assertEqual(self.create('two')[0]['operation'],'unchanged')
        self.assertEqual(len(self.events()),2)

    def test_another_process_invalidates_cache(self):
        self.create()
        code = "import signal_ledger as l; l.delete_signal(l.SignalDelete(signal_id='one',revision=1),owner='alice')"
        subprocess.run([sys.executable,'-c',code],cwd=str(Path(ledger.__file__).parent),check=True)
        self.assertEqual(ledger.get_signal('one',owner='alice')['status'],'deleted')

    def test_in_place_edit_invalidates_cache(self):
        self.create()
        path = self.root/'ledger.csv'
        path.write_bytes(path.read_bytes().replace(b'110',b'120'))
        self.assertEqual(ledger.get_signal('one',owner='alice')['current']['price'],120)

    def test_threaded_retries_only_create_once(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(lambda _:self.create(),range(30)))
        self.assertEqual(sum(r[0]['operation']=='created' for r in results),1)
        self.assertEqual(len(self.events()),1)

    def test_parallel_distinct_creates_are_not_lost(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            list(pool.map(lambda i:self.create('id'+str(i)),range(50)))
        self.assertEqual(len(self.events()),50)
        self.assertEqual(len(ledger.list_signals(owner='alice')),50)

    def test_owner_and_revision_checks_remain(self):
        self.create()
        for owner,revision,expected in [('bob',1,404),('alice',2,409)]:
            with self.assertRaises(HTTPException) as error:
                ledger.delete_signal(ledger.SignalDelete(signal_id='one',revision=revision),owner=owner)
            self.assertEqual(error.exception.status_code,expected)
        self.assertEqual(len(self.events()),1)

    def test_legacy_columns_and_missing_final_newline(self):
        self.create()
        rows = self.events()
        with (self.root/'ledger.csv').open('w',newline='') as f:
            writer = csv.DictWriter(f,fieldnames=ledger.LEGACY_LEDGER_FIELDS,extrasaction='ignore')
            writer.writeheader()
            writer.writerows(rows)
        self.create('two')
        self.assertEqual(len(self.events()),2)
        path = self.root/'ledger.csv'
        path.write_bytes(path.read_bytes().rstrip(b'\r\n'))
        self.create('three')
        self.assertEqual(len(self.events()),3)

    def test_export_csv_and_history(self):
        self.create('one',110)
        self.create('two',90)
        quotes = {ticker:{'price':'100','source':'test','timestamp':time.time(),'fetched_at':time.time()} for ticker in ['ONE','TWO']}
        with patch.object(ledger,'fetch_quotes',return_value=quotes):
            result = ledger.export_live(operator='exporter')
        self.assertEqual(result['rows'],2)
        with next((self.root/'exports').glob('*.csv')).open() as f:
            self.assertEqual(list(csv.reader(f)),[['account','symbol','target'],['TEST','ONE','0.05'],['TEST','TWO','-0.05']])
        record = ledger.get_signal('one',owner='alice')
        self.assertEqual([e['event'] for e in record['history']],['created','exported'])
        self.assertEqual(len(self.events()),4)
        with self.assertRaises(HTTPException) as error:
            ledger.export_live(operator='exporter')
        self.assertEqual(error.exception.status_code,409)

    def test_freshness_requires_current_fetch_and_recent_market_timestamp(self):
        now=time.time()
        with patch.object(ledger.time,'time',return_value=now):
            ledger.require_fresh_quotes({'TEST':{'timestamp':now-60,'fetched_at':now}})
            for quote,message in [
                    ({'timestamp':now-60,'fetched_at':now-31},'refreshed'),
                    ({'timestamp':now-5401,'fetched_at':now},'stale'),
                    ({'timestamp':now-60},'refreshed')]:
                with self.subTest(quote=quote), self.assertRaisesRegex(RuntimeError,message):
                    ledger.require_fresh_quotes({'TEST':quote})

    def test_fetch_quote_bypasses_cache_and_marks_fetch_time(self):
        class Response:
            def raise_for_status(self): pass
            def json(self): return {'symbol':'TEST','close':'100','timestamp':123}
        with patch.dict(os.environ,{'TWELVE_DATA_API_KEY':'test'}), \
                patch.object(ledger.requests,'get',return_value=Response()) as get, \
                patch.object(ledger.time,'time',return_value=456):
            quote=ledger.fetch_quotes(['TEST'])['TEST']
        self.assertEqual(quote['fetched_at'],456)
        self.assertEqual(get.call_args.kwargs['headers']['Cache-Control'],'no-cache')

    def test_clear_live_preserves_history_and_only_clears_pending_live(self):
        self.create('live')
        ledger.delete_signal(ledger.SignalDelete(signal_id='live',revision=1),owner='alice')
        self.create('pending')
        result=ledger.clear_live(operator='admin')
        self.assertEqual(result,{'cleared':1})
        cleared=ledger.get_signal('pending',owner='alice')
        self.assertEqual(cleared['status'],'cleared')
        self.assertEqual(cleared['reason'],'cleared_by_admin')
        self.assertEqual([e['event'] for e in cleared['history']],['created','cleared'])
        self.assertEqual(ledger.get_signal('live',owner='alice')['status'],'deleted')
        with self.assertRaises(HTTPException) as error:
            ledger.clear_live(operator='admin')
        self.assertEqual(error.exception.status_code,409)


if __name__ == '__main__':
    unittest.main()
