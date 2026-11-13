"""HTTP abuse regressions against an isolated service with synthetic market prices."""
import base64
import csv
import io
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request


class ApiAbuseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='signal-abuse-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.tokens = {name:secrets.token_hex(32) for name in ['alice','bob','exporter','admin']}
        for scope,names in [('senders',['alice','bob']),('exporters',['exporter']),('admins',['admin'])]:
            (cls.root/(scope+'.htpasswd')).write_text(''.join(name+':{SHA}'+
                base64.b64encode(hashlib.sha1(cls.tokens[name].encode()).digest()).decode()+'\n' for name in names))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0))
            port=sock.getsockname()[1]
        cls.base=f'http://127.0.0.1:{port}'
        env={**os.environ,'SIGNAL_DATA_DIR':str(cls.root),
             'SIGNAL_SENDER_KEYS_FILE':str(cls.root/'senders.htpasswd'),
             'SIGNAL_EXPORTER_KEYS_FILE':str(cls.root/'exporters.htpasswd'),
             'SIGNAL_ADMIN_KEYS_FILE':str(cls.root/'admins.htpasswd'),'SIGNAL_MAX_QUOTE_AGE_SECONDS':'300',
             'SIGNAL_LIVE_ACCOUNT':'TEST','TWELVE_DATA_API_KEY':'test-not-a-real-key',
             'PAPER_QUEUE_DIR':str(cls.root/'paper'),'PAPER_SUBMIT_TOKEN':'test-submit-'*5,
             'PAPER_WORKER_TOKEN':'test-worker-'*5,
             'PYTHONPATH':str(Path(__file__).resolve().parents[1])}
        code=f"import app,uvicorn,signal_ledger,time; app.get_price=lambda ticker:100; signal_ledger.lseg_prices=lambda tickers: {{t:100 for t in tickers}}; signal_ledger.fetch_quotes=lambda symbols: {{s:{{'price':'100','source':'test','timestamp':time.time(),'fetched_at':time.time()}} for s in symbols}}; uvicorn.run(app.app,host='127.0.0.1',port={port},access_log=False,log_level='critical')"
        cls.server=subprocess.Popen([sys.executable,'-c',code],env=env,cwd=cls.root,
                                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        def stop():
            cls.server.terminate()
            try: cls.server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.server.kill();cls.server.wait()
        cls.addClassCleanup(stop)
        for _ in range(100):
            try:
                if cls.request('/signals',owner=None)[0]==401: break
            except OSError: time.sleep(.05)
        else: raise RuntimeError('Isolated server failed to start')

    @classmethod
    def request(cls,path,body=None,owner='alice'):
        headers={'Content-Type':'application/json'}
        if owner: headers['Authorization']='Bearer '+cls.tokens[owner]
        req=urllib.request.Request(cls.base+path,headers=headers,
            data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req,timeout=5) as response:
                return response.status,response.read()
        except urllib.error.HTTPError as error:
            with error:
                return error.code,error.read()

    def signal(self,ident):
        return dict(signal_id=ident,destination='LIVE',ticker='TEST',price=110,timestamp=123)

    def test_admin_can_send_edit_delete_and_export_with_one_key(self):
        self.assertEqual(self.request('/signals',self.signal('admin-edit'),'admin')[0],202)
        self.assertEqual(self.request('/signals/edit',dict(signal_id='admin-edit',revision=1,price=120),'admin')[0],200)
        self.assertEqual(self.request('/signals/delete',dict(signal_id='admin-edit',revision=2),'admin')[0],200)
        self.assertEqual(self.request('/signals',self.signal('admin-export'),'admin')[0],202)
        code,body=self.request('/signals/export',{},'admin')
        self.assertEqual(code,200)
        self.assertEqual(json.loads(body)['rows'],1)
        self.assertEqual(json.loads(self.request('/signals/admin-export',owner='admin')[1])['status'],'exported')
        self.assertEqual(self.request('/paper-signals/claim',{},'admin')[0],401)

    def test_only_admin_can_clear_all_pending_live_signals(self):
        self.assertEqual(self.request('/signals',self.signal('clear-alice'),'alice')[0],202)
        self.assertEqual(self.request('/signals',self.signal('clear-bob'),'bob')[0],202)
        for owner in [None,'alice','bob','exporter']:
            self.assertEqual(self.request('/signals/clear',{},owner)[0],401)
        code,body=self.request('/signals/clear',{},'admin')
        self.assertEqual(code,200)
        self.assertGreaterEqual(json.loads(body)['cleared'],2)
        for owner,ident in [('alice','clear-alice'),('bob','clear-bob')]:
            record=json.loads(self.request('/signals/'+ident,owner=owner)[1])
            self.assertEqual(record['status'],'cleared')
            self.assertEqual(record['reason'],'cleared_by_admin')
        self.assertEqual(self.request('/signals/clear',{},'admin')[0],409)

    def test_cross_owner_read_edit_delete_and_collision(self):
        original=self.signal('private')
        self.assertEqual(self.request('/signals',original)[0],202)
        self.assertEqual(self.request('/signals/private',owner='bob')[0],404)
        for route,body in [('/signals/edit',dict(signal_id='private',revision=1,price=999)),
                           ('/signals/delete',dict(signal_id='private',revision=1))]:
            self.assertEqual(self.request(route,body,'bob')[0],404)
        self.assertEqual(self.request('/signals',original,'bob')[0],409)
        own=json.loads(self.request('/signals/private')[1])
        self.assertEqual(own['current']['price'],110)
        self.assertEqual(own['revision'],1)

    def test_key_scopes_and_missing_credentials(self):
        for owner in [None,'alice','bob']:
            self.assertEqual(self.request('/signals/export',{},owner)[0],401)
        self.assertEqual(self.request('/signals',self.signal('bad-scope'),'exporter')[0],401)
        self.assertEqual(self.request('/reset',{},'alice')[0],401)
        self.assertEqual(self.request('/portfolio',owner='alice')[0],401)

    def test_every_sender_downloads_everyones_pending_live_signals(self):
        self.assertEqual(self.request('/signals',dict(self.signal('csv-alice'),ticker='ZBH.N'),'alice')[0],202)
        self.assertEqual(self.request('/signals',dict(self.signal('csv-bob'),ticker='ZBRA',price=90),'bob')[0],202)
        for owner in ['alice','bob','admin']:
            code,body=self.request('/signals/pending',owner=owner)
            self.assertEqual(code,200)
            listed={row['signal_id']:row['analyst'] for row in json.loads(body)}
            self.assertEqual((listed['csv-alice'],listed['csv-bob']),('alice','bob'))
            code,body=self.request('/signals/portfolio.csv',owner=owner)
            self.assertEqual(code,200)
            rows=list(csv.reader(io.StringIO(body.decode())))
            self.assertEqual(rows[0],['account','symbol','target'])
            targets={symbol:float(target) for account,symbol,target in rows[1:]}
            self.assertGreater(targets['ZBH'],0)  # target 110 above the stubbed LSEG price of 100
            self.assertLess(targets['ZBRA'],0)
            self.assertLessEqual(sum(abs(t) for t in targets.values()),1+1e-6)
        for route in ['/signals/pending','/signals/portfolio.csv']:
            for owner in [None,'exporter']:
                self.assertEqual(self.request(route,owner=owner)[0],401)
        self.assertEqual(json.loads(self.request('/signals/csv-alice')[1])['status'],'pending')  # download changes nothing

    def test_nonfinite_numbers_are_client_errors_not_server_errors(self):
        for value in [float('nan'),float('inf'),float('-inf')]:
            with self.subTest(value=value):
                body={**self.signal('nonfinite'),'price':value}
                self.assertEqual(self.request('/signals',body)[0],422)

    def test_legacy_route_rejects_invalid_values(self):
        for fields in [{'price':-1},{'price':float('inf')},{'ticker':'=1+1'},
                       {'ticker':'AAPL\nFAKE'},{'timestamp':-1}]:
            with self.subTest(fields=fields):
                body=dict(researcher='alice',ticker='TEST',price=110,timestamp=123)
                self.assertEqual(self.request('/prediction',{**body,**fields})[0],422)

    def test_valid_legacy_submission_still_works(self):
        body=dict(researcher='alice',ticker='TEST',price=110,timestamp=123)
        self.assertEqual(self.request('/prediction',body)[0],200)
        self.assertEqual(self.request('/prediction',{**body,'researcher':'bob'})[0],403)

    def test_validation_errors_do_not_echo_attacker_input(self):
        code,body=self.request('/signals',{**self.signal('invalid-input'),'price':float('nan')})
        self.assertEqual(code,422)
        errors=json.loads(body)['detail']
        self.assertTrue(errors)
        self.assertTrue(all(set(item)=={'type','loc','msg'} for item in errors))

    def test_path_injection_and_mass_assignment_rejected(self):
        for fields in [{'signal_id':'../escape'},{'ticker':'=1+1'},
                       {'owner':'bob'},{'status':'exported'},{'revision':999}]:
            self.assertEqual(self.request('/signals',{**self.signal('invalid'),**fields})[0],422)

    def test_bulk_conflict_does_not_partially_commit(self):
        self.assertEqual(self.request('/signals',self.signal('taken'),'bob')[0],202)
        self.assertEqual(self.request('/signals',[self.signal('rollback'),self.signal('taken')])[0],409)
        self.assertEqual(self.request('/signals/rollback')[0],404)


if __name__=='__main__':
    unittest.main()
