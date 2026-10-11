import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from test_controller import FakeWDA
from iphone_use import Runtime, validate_semantics
from wda_client import WDAError
from wda_controller import PhoneController


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.client=FakeWDA();self.phone=PhoneController(self.client,self.directory.name)
        self.auth={"scope":"app","target":self.client.app}
        self.client.elements[0]["kind"]="XCUIElementTypeSecureTextField"

    def test_secure_default_remains_denied(self):
        with self.assertRaises(WDAError):self.phone.type_text({"label":"Target"},"fixture")
        self.assertEqual(self.client.actions(),[])

    def test_scoped_secure_input_no_readback_or_observation(self):
        result=self.phone.type_text({"label":"Target"},"test-password",test_authorization=self.auth)
        self.assertTrue(result["input_complete"])
        self.assertNotIn("test-password",json.dumps(result))
        self.assertFalse(result["exact_readback"])
        self.assertFalse(any(p in ('/screenshot','/source') for _,p,_ in self.client.calls))
        self.assertIsNone(self.phone.pending_input)

    def test_wrong_app_has_no_mutation(self):
        with self.assertRaises(WDAError):self.phone.type_text({"label":"Target"},"fixture",test_authorization={"scope":"app","target":"wrong.app"})
        self.assertEqual(self.client.actions(),[])

    def test_forbidden_options_do_not_capture_or_mutate(self):
        for option in ({"verify":True},{"observe":"both"},{"allow_newlines":True},{"continue_token":"bad"}):
            self.client.calls.clear()
            with self.assertRaises(WDAError):self.phone.type_text({"label":"Target"},"fixture",test_authorization=self.auth,**option)
            self.assertEqual(self.client.actions(),[])
            self.assertFalse(any(p=='/screenshot' for _,p,_ in self.client.calls))

    def test_missing_keypad_stops_without_input(self):
        self.client.locked=True;self.client.app='com.apple.springboard'
        with patch.object(self.phone,'query',return_value={"matches":0}):
            with self.assertRaises(WDAError):self.phone.type_text(text='123456',test_authorization={"scope":"device_unlock","target":"test-device"})
        self.assertEqual(self.client.actions(),[])

    def test_unlock_attempt_persisted_without_code_and_cooldown(self):
        self.client.locked=True;self.client.app='com.apple.springboard'
        element={"path":"unused"}
        with patch.object(self.phone,'query',return_value={"matches":1}),patch.object(self.phone,'resolve',return_value=element),patch.object(self.phone,'element_rect',return_value={"x":10,"y":10,"width":20,"height":20}),patch.object(self.phone,'post') as post:
            result=self.phone.type_text(text='123456',test_authorization={"scope":"device_unlock","target":"test-device"})
            self.assertEqual(post.call_count,6);self.assertFalse(result['unlocked'])
            saved=(Path(self.directory.name)/'unlock-attempt.json').read_text();self.assertNotIn('123456',saved)
            with self.assertRaises(WDAError) as caught:self.phone.type_text(text='123456',test_authorization={"scope":"device_unlock","target":"test-device"})
            self.assertEqual(caught.exception.code,'unlock_cooldown');self.assertEqual(post.call_count,6)

    def test_runtime_wrong_device_and_error_redaction(self):
        runtime=Runtime(self.directory.name);self.addCleanup(runtime.close)
        runtime.client=self.client;self.client.close=lambda:None;self.client.credentials_redacted=True;runtime.phone=self.phone
        runtime.screen=Mock();runtime.setup_manager=Mock();runtime.setup_manager.config={"udid":"test-device"}
        with self.assertRaises(WDAError) as caught:runtime.call('pua_type_text',{'text':'123456','test_authorization':{'scope':'device_unlock','target':'other-device'}})
        self.assertEqual(caught.exception.code,'test_scope_mismatch');self.assertEqual(self.client.actions(),[])
        with patch.object(self.phone,'type_text',side_effect=WDAError('http_error','fixture-secret',details={'body':'fixture-secret','action_executed':True})):
            with self.assertRaises(WDAError) as caught:runtime.call('pua_type_text',{'text':'fixture-secret','test_authorization':self.auth})
        self.assertNotIn('fixture-secret',json.dumps(caught.exception.as_dict()))
        runtime.screen.set_paused.assert_called_with(True,reason='authentication')


    def test_runtime_refuses_old_runner_before_credential_input(self):
        runtime=Runtime(self.directory.name);self.addCleanup(runtime.close)
        runtime.client=self.client;self.client.close=lambda:None;runtime.phone=self.phone;runtime.screen=Mock()
        with self.assertRaises(WDAError) as caught:runtime.call('pua_type_text',{'text':'fixture','test_authorization':self.auth})
        self.assertEqual(caught.exception.details['cause'],'not_ready');self.assertEqual(self.client.actions(),[])

    def test_credentials_cannot_be_batched(self):
        with self.assertRaises(WDAError):validate_semantics('batch',{'steps':[{'op':'type_text','args':{'text':'fixture','test_authorization':self.auth}}]})
