import unittest
from unittest.mock import patch
from codex_resume.__main__ import main
from codex_resume.app import AppError

class ProbeTests(unittest.TestCase):
    def run_probe(self, response):
        with patch('codex_resume.__main__.compatibility_report',return_value={'app':{'version':'unverified'},'versionVerified':False}), \
             patch('codex_resume.__main__.ReadOnlyServer') as server, \
             patch('codex_resume.__main__.Desktop') as desktop, \
             patch('codex_resume.__main__.Store') as store, \
             patch('codex_resume.__main__.output') as output:
            reader=server.return_value.__enter__.return_value
            reader.query.return_value=response
            try:
                main(['probe'])
            finally:
                reader.query.assert_called_once_with('account/rateLimits/read')
                desktop.assert_not_called();store.assert_not_called()
            return output.call_args.args[0]
    def test_unverified_version_can_probe_without_enabling_resume_or_exposing_quota(self):
        for quota in [{'rateLimits':{'primary':{'usedPercent':42}}},
                      {'rateLimitsByLimitId':{'codex':{'primary':{'usedPercent':42}}}}]:
            result=self.run_probe(quota)
            self.assertFalse(result['versionVerified']);self.assertFalse(result['resumeVerified'])
            self.assertEqual(result['appIPC'],'not_checked')
            self.assertNotIn('usedPercent',str(result));self.assertNotIn('ready',result)
    def test_malformed_or_empty_response_is_not_success(self):
        for value in [None,[],{}, {'rateLimits':None}, {'rateLimits':{}}, {'rateLimitsByLimitId':[]}]:
            with self.subTest(value=value),self.assertRaises(RuntimeError):self.run_probe(value)
    def test_unrecognized_install_does_not_launch_query_server(self):
        with patch('codex_resume.__main__.compatibility_report',side_effect=AppError('unknown app')), \
             patch('codex_resume.__main__.ReadOnlyServer') as server:
            with self.assertRaises(AppError):main(['probe'])
            server.assert_not_called()
