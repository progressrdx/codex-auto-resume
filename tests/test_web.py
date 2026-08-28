"""HTTP boundary tests use a fake backend, never a real account or business task."""
import http.client
import json
import shutil
import ssl
import subprocess
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from codex_resume.web import APIError, Backend, CompanionServer, serve

THREAD = '11111111-1111-4111-8111-111111111111'


class FakeBackend:
    def __init__(self):
        self.calls = []
    def watches(self): return []
    def threads(self): return [{'id': THREAD, 'title': '<script>not markup</script>'}]
    def quota(self): return {'ready': False}
    def check(self, value):
        self.calls.append(('check', value))
        return {'threadId': value, 'decision': 'stop'}
    def mutate(self, action, data):
        self.calls.append((action, data))
        return {'ok': True}


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = FakeBackend()
        cls.server = CompanionServer(('127.0.0.1', 0), cls.backend, token='test-credential')
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.worker.join()
    def setUp(self): self.backend.calls.clear()
    def request(self, method, path, body=None, headers=None, auth=True):
        c = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        h = {'X-Resume-Token':'test-credential'} if auth else {}
        if headers: h.update(headers)
        c.request(method, path, body=body, headers=h)
        response = c.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        c.close()
        return result
    def test_static_shell_has_no_secret_and_does_not_read_tasks(self):
        status, headers, body = self.request('GET', '/', auth=False)
        self.assertEqual(status, 200)
        self.assertNotIn(b'test-credential', body)
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
    def test_all_private_gets_require_authentication(self):
        for path in ['/api/watches','/api/threads','/api/quota','/api/check?threadId='+THREAD]:
            self.assertEqual(self.request('GET',path,auth=False)[0],401)
        self.assertEqual(self.backend.calls, [])
    def test_bad_token_and_cross_site_requests_never_mutate(self):
        for headers, expected in [({'X-Resume-Token':'wrong'},401),
                                  ({'Origin':'https://other.example'},403),
                                  ({'Host':'attacker.example'},403),
                                  ({'Sec-Fetch-Site':'cross-site'},403)]:
            headers['Content-Type']='application/json'
            self.assertEqual(self.request('POST','/api/start',json.dumps({'threadId':THREAD}),headers)[0],expected)
        self.assertEqual(self.backend.calls, [])
    def test_same_origin_json_post(self):
        body={'threadId':THREAD,'confirmed':True,'maxResumes':1}
        code, _, _ = self.request('POST','/api/start',json.dumps(body),{'Content-Type':'application/json','Origin':self.server.origin})
        self.assertEqual(code,200)
        self.assertEqual(self.backend.calls,[('start',body)])
    def test_miniprogram_referer_works_without_opening_cross_origin_access(self):
        code, _, _ = self.request('GET','/api/watches',headers={
            'Referer':'https://servicewechat.com/test-app/devtools/page-frame.html'})
        self.assertEqual(code,200)
        self.assertEqual(self.request('GET','/api/watches',headers={
            'Referer':'https://servicewechat.com/test-app/devtools/page-frame.html',
            'Origin':'https://untrusted.example'})[0],403)
    def test_explicit_miniprogram_fetch_metadata_exception_remains_authenticated_and_scoped(self):
        appid='wx14176cf92cfb62b9'
        headers={'Referer':f'https://servicewechat.com/{appid}/devtools/page-frame.html',
                 'Sec-Fetch-Site':'cross-site'}
        self.assertEqual(self.request('GET','/api/watches',headers=headers)[0],403)
        self.server.mini_app_id=appid
        try:
            self.assertEqual(self.request('GET','/api/watches',headers=headers)[0],200)
            self.assertEqual(self.request('GET','/api/watches',headers=dict(headers,**{'Sec-Fetch-Site':'same-site'}))[0],200)
            self.assertEqual(self.request('GET','/api/watches',headers=headers,auth=False)[0],401)
            for bad in [dict(headers,Origin='https://servicewechat.com'),
                        dict(headers,Referer='https://servicewechat.com/wx0000000000000000/devtools/page-frame.html'),
                        dict(headers,Referer=headers['Referer']+'?x=1'),
                        dict(headers,Host='other.example')]:
                self.assertEqual(self.request('GET','/api/watches',headers=bad)[0],403)
            self.assertEqual(self.request('GET','/',headers=headers)[0],403)
            headers['Content-Type']='application/json'
            body={'threadId':THREAD,'confirmed':True,'maxResumes':1}
            code, response_headers, _ = self.request('POST','/api/start',json.dumps(body),headers)
            self.assertEqual(code,200)
            self.assertEqual(self.backend.calls,[('start',body)])
            self.assertNotIn('Access-Control-Allow-Origin',response_headers)
        finally:
            self.server.mini_app_id=None
    def test_get_cannot_start_and_unknown_paths_do_not_escape_static_root(self):
        for path in ['/api/start','/../web.py','/%2e%2e/web.py','/app.js?secret=123']:
            self.assertEqual(self.request('GET',path)[0],404)
        self.assertEqual(self.backend.calls, [])
    def test_check_requires_exactly_one_id(self):
        for path in ['/api/check','/api/check?threadId='+THREAD+'&threadId='+THREAD]:
            self.assertEqual(self.request('GET',path)[0],400)
        self.assertEqual(self.request('GET','/api/check?threadId='+THREAD)[0],200)
    def test_malformed_large_and_non_json_requests_rejected(self):
        for body, headers, expected in [('{}', {'Content-Type':'text/plain'},415),
                                        ('{', {'Content-Type':'application/json'},400),
                                        ('x'*2049, {'Content-Type':'application/json'},413)]:
            self.assertEqual(self.request('POST','/api/start',body,headers)[0],expected)
        self.assertEqual(self.backend.calls, [])
    def test_no_cors_or_reflected_private_error(self):
        with patch.object(self.backend,'quota',side_effect=Exception('private auth dump')):
            code, headers, body=self.request('GET','/api/quota')
        self.assertEqual(code,500)
        self.assertNotIn(b'private auth dump',body)
        self.assertNotIn('Access-Control-Allow-Origin',headers)
    def test_assets_served_and_manifest_is_valid(self):
        for path in ['/app.js','/style.css','/icon.svg','/manifest.webmanifest']:
            code, _, body=self.request('GET',path,auth=False)
            self.assertEqual(code,200)
            if 'manifest' in path: self.assertEqual(json.loads(body)['start_url'],'/')


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.backend=Backend(SimpleNamespace(home=Path('/tmp/home'),app=Path('/tmp/app'),state_dir=Path('/tmp/state')))
    def test_start_requires_confirmation_uuid_budget_and_only_supported_fields(self):
        good={'threadId':THREAD,'confirmed':True,'maxResumes':1}
        cases=[{}, [], dict(good,confirmed=False),dict(good,threadId='; rm file'),
               dict(good,maxResumes=True),dict(good,maxResumes=0),dict(good,maxResumes=101),
               dict(good,maxResumes='3'),dict(good,prompt='run anything'),dict(good,limitId='--arg')]
        with patch.object(self.backend,'command') as command:
            for body in cases:
                with self.subTest(body=body), self.assertRaises(APIError): self.backend.mutate('start',body)
            command.assert_not_called()
    def test_start_calls_guarded_cli_and_does_not_expose_arbitrary_prompt(self):
        with patch.object(self.backend,'command',return_value={'status':'starting'}) as command:
            self.backend.mutate('start',{'threadId':THREAD,'confirmed':True,'limitId':'codex'})
            command.assert_called_once_with('start',THREAD,'--max-resumes','3','--limit-id','codex')
    def test_concurrent_start_refused_and_stop_runs_after_pending_start(self):
        self.backend.mutation_lock.acquire()
        entered = threading.Event()
        done = threading.Event()
        def stop():
            entered.set()
            self.backend.mutate('stop', {'threadId': THREAD})
            done.set()
        with patch.object(self.backend, 'command') as command:
            try:
                with self.assertRaises(APIError): self.backend.mutate('start', {'threadId': THREAD, 'confirmed': True})
                worker = threading.Thread(target=stop)
                worker.start()
                self.assertTrue(entered.wait(1))
                self.assertFalse(done.wait(.05))
                command.assert_not_called()
            finally:
                self.backend.mutation_lock.release()
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertTrue(done.is_set())
            command.assert_called_once_with('stop', THREAD)
    def test_quota_cached_without_exposing_credits(self):
        raw={'app':{'version':'test'},'ipc':'connected','ready':True,'reason':'ok',
             'rateLimits':{'primary':{'usedPercent':10},'credits':{'balance':'secret'}}}
        with patch.object(self.backend,'command',return_value=raw) as command:
            first=self.backend.quota(); second=self.backend.quota()
            self.assertEqual(first,second); self.assertEqual(command.call_count,1)
            self.assertNotIn('credits',first)
    def test_command_timeout_no_retry(self):
        import subprocess
        with patch('codex_resume.web.subprocess.run',side_effect=subprocess.TimeoutExpired('test',65)) as run:
            with self.assertRaises(APIError): self.backend.command('start',THREAD)
            self.assertEqual(run.call_count,1)
    def test_http_over_lan_and_wildcard_bind_rejected_before_listening(self):
        for host in ['0.0.0.0','192.168.1.20','::1','some-domain.test']:
            args=SimpleNamespace(host=host,port=0,certfile=None,keyfile=None)
            with patch('codex_resume.web.CompanionServer') as server:
                with self.subTest(host=host),self.assertRaises(RuntimeError): serve(args)
                server.assert_not_called()


class HTTPSTests(unittest.TestCase):
    def test_public_origin_is_explicit_https_only_without_path_or_credentials(self):
        for value in ['http://relay.example','https://relay.example/','https://user@relay.example',
                      'https://relay.example?x=y','https://*.example','https://relay..example',
                      'https://-relay.example','https://relay.example:0','https://relay.example:99999']:
            with self.subTest(value=value),self.assertRaises(RuntimeError):
                CompanionServer(('127.0.0.1',0),FakeBackend(),secure=True,public_origin=value)
        with self.assertRaises(RuntimeError):
            CompanionServer(('127.0.0.1',0),FakeBackend(),public_origin='https://relay.example')
        with CompanionServer(('127.0.0.1',0),FakeBackend(),secure=True,
                             public_origin='https://relay.example:8765') as server:
            self.assertEqual(server.authority,'relay.example:8765')
            self.assertEqual(server.origin,'https://relay.example:8765')
    @unittest.skipUnless(shutil.which('openssl'), 'optional local TLS test requires openssl')
    def test_tls_with_explicit_certificate_trust_and_authentication(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / 'openssl.cnf'
            config.write_text('[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=ext\n'
                              '[dn]\nCN=127.0.0.1\n[ext]\nsubjectAltName=IP:127.0.0.1\n'
                              'basicConstraints=critical,CA:TRUE\n')
            cert, key = root / 'cert.pem', root / 'key.pem'
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes',
                            '-days','1','-keyout',str(key),'-out',str(cert),'-config',str(config)],
                           check=True, capture_output=True, timeout=20)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(cert,key)
            server = CompanionServer(('127.0.0.1',0),FakeBackend(),token='tls-test',secure=True)
            server.socket = context.wrap_socket(server.socket,server_side=True)
            worker=threading.Thread(target=server.serve_forever,daemon=True); worker.start()
            try:
                client_context=ssl.create_default_context(cafile=str(cert))
                for headers, expected in [({},401),({'X-Resume-Token':'tls-test'},200)]:
                    client=http.client.HTTPSConnection(*server.server_address,context=client_context,timeout=3)
                    client.request('GET','/api/watches',headers=headers)
                    response=client.getresponse()
                    self.assertEqual(response.status,expected)
                    response.read(); client.close()
                # Same TLS listener with one explicitly configured external authority.
                server.authority = 'relay.example:8765'
                server.origin = 'https://relay.example:8765'
                for headers, expected in [({'Host':'relay.example:8765'},200),
                        ({'Host':'other.example:8765'},403),
                        ({'Host':'relay.example:8765','Origin':'https://other.example'},403)]:
                    headers['X-Resume-Token']='tls-test'
                    client=http.client.HTTPSConnection(*server.server_address,context=client_context,timeout=3)
                    client.request('GET','/api/watches',headers=headers)
                    response=client.getresponse(); self.assertEqual(response.status,expected)
                    response.read(); client.close()
            finally:
                server.shutdown(); server.server_close(); worker.join()


if __name__=='__main__': unittest.main()
