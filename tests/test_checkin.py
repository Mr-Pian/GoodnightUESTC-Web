"""Manual submission tests use synthetic school responses only."""
import tempfile
import hashlib
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch
from checkin import Attempts, SubmissionTransport, SUBMIT_URL, prepare_checkin, submit_checkin
from core import ToolError, normalize_location
from web_support import SNAPSHOT, POINT, INSTANT, DAY, WebAppTestCase

SCHEDULE = {'effective': True, 'studentCheckinStartOffsetMinute': 1260,
            'studentCheckinEndOffsetMinute': 1410}


def record(status='PENDING'):
    return {'businessDate': DAY, 'recordStatusCode': status, 'abnormalFlag': False,
            'checkinTime': DAY+' 22:00:00' if status == 'CHECKED_IN' else None}


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.attempts = Attempts(Path(self.temp.name))
        self.school = MagicMock()
        self.school.enter.return_value = record()
        self.school.today.return_value = record()
        self.school.schedule.return_value = SCHEDULE
        self.school.locations.return_value = [{**normalize_location(POINT)['gcj02'], 'radiusMeter': 150}]
        self.school.reverse_geocode.return_value = '合成测试地址'
        self.school_patch = patch('checkin.school_for', return_value=self.school)
        self.school_patch.start()
        self.clock_patch = patch('checkin.now', return_value=INSTANT)
        self.clock_patch.start()

    def tearDown(self):
        self.clock_patch.stop()
        self.school_patch.stop()
        self.temp.cleanup()

    def test_prepare_reads_only_and_returns_address(self):
        preview = prepare_checkin(SNAPSHOT, POINT)
        self.assertEqual(preview['address'], '合成测试地址')
        self.assertFalse(preview['submitted'])
        self.school.submit.assert_not_called()

    def test_confirmed_only_after_readback(self):
        self.school.today.side_effect = [record(), record(), record('CHECKED_IN')]
        result = submit_checkin(SNAPSHOT, POINT, self.attempts, sleep=lambda _: None)
        self.assertTrue(result['confirmed'])
        self.assertEqual(result['result'], 'confirmed')
        self.school.submit.assert_called_once()
        payload = self.school.submit.call_args.args[0]
        self.assertEqual(payload['reverseGeocode'], '合成测试地址')
        self.assertEqual(payload['coordinateSystemCode'], 'gcj02')

    def test_timeout_never_causes_second_post_even_after_restart(self):
        self.school.submit.side_effect = ToolError('timeout', 'checkin_post')
        result = submit_checkin(SNAPSHOT, POINT, self.attempts, sleep=lambda _: None)
        self.assertEqual(result['result'], 'unknown')
        restored = Attempts(Path(self.temp.name))
        second = submit_checkin(SNAPSHOT, POINT, restored, sleep=lambda _: None)
        self.assertEqual(second['result'], 'prior_attempt')
        self.assertFalse(second['submitted'])
        self.school.submit.assert_called_once()

    def test_explicit_rejection_can_be_retried_after_restart_and_fresh_checks(self):
        self.school.submit.side_effect = ToolError('business_error', 'checkin_post', 4001,
                                                  http_status=200, school_message='请求被拒绝')
        first = submit_checkin(SNAPSHOT, POINT, self.attempts, sleep=lambda _: None)
        self.assertEqual(first['result'], 'rejected')
        self.assertTrue(first['retryAllowed'])
        self.assertEqual(first['apiCode'], 4001)
        self.assertEqual(first['httpStatus'], 200)
        self.assertEqual(first['schoolMessage'], '请求被拒绝')
        self.school.submit.assert_called_once()  # No automatic POST retry.

        self.school.reset_mock()
        self.school.submit.side_effect = None
        self.school.today.side_effect = [record(), record(), record('CHECKED_IN')]
        second = submit_checkin(SNAPSHOT, POINT, Attempts(Path(self.temp.name)), sleep=lambda _: None)
        self.assertEqual(second['result'], 'confirmed')
        self.school.submit.assert_called_once()
        self.school.locations.assert_called_once()
        self.school.schedule.assert_called_once()
        self.assertEqual(self.school.today.call_count, 3)

    def test_old_rejected_row_does_not_bypass_school_record_or_window(self):
        key = hashlib.sha256(SNAPSHOT['student_id'].encode()).hexdigest()
        self.attempts.claim(key, DAY)
        self.attempts.finish(key, DAY, 'rejected')
        self.school.schedule.return_value = {**SCHEDULE, 'studentCheckinStartOffsetMinute': 0,
                                            'studentCheckinEndOffsetMinute': 30}
        with self.assertRaises(ToolError) as error:
            submit_checkin(SNAPSHOT, POINT, self.attempts)
        self.assertEqual(error.exception.reason, 'outside_checkin_window')
        self.assertEqual(self.attempts.outcome(key, DAY), 'rejected')
        self.school.today.return_value = record('CHECKED_IN')
        result = submit_checkin(SNAPSHOT, POINT, self.attempts)
        self.assertEqual(result['result'], 'already_recorded')
        self.school.submit.assert_not_called()

    def test_rejection_then_ambiguous_retry_remains_protected(self):
        self.school.submit.side_effect = ToolError('business_error', 'checkin_post', 4001)
        submit_checkin(SNAPSHOT, POINT, self.attempts, sleep=lambda _: None)
        self.school.submit.side_effect = ToolError('timeout', 'checkin_post')
        retry = submit_checkin(SNAPSHOT, POINT, self.attempts, sleep=lambda _: None)
        self.assertEqual(retry['result'], 'unknown')
        self.assertFalse(retry['retryAllowed'])
        blocked = submit_checkin(SNAPSHOT, POINT, Attempts(Path(self.temp.name)))
        self.assertEqual(blocked['result'], 'prior_attempt')
        self.assertEqual(self.school.submit.call_count, 2)

    def test_rejection_not_saved_does_not_offer_retry(self):
        self.school.submit.side_effect = ToolError('business_error', 'checkin_post', 4001)
        with patch.object(self.attempts, 'finish', side_effect=sqlite3.OperationalError('synthetic failure')):
            result = submit_checkin(SNAPSHOT, POINT, self.attempts, sleep=lambda _: None)
        self.assertEqual(result['result'], 'rejected')
        self.assertFalse(result['retryAllowed'])
        self.assertFalse(result['attemptStateSaved'])
        blocked = submit_checkin(SNAPSHOT, POINT, self.attempts)
        self.assertEqual(blocked['result'], 'prior_attempt')
        self.school.submit.assert_called_once()

    def test_rejected_response_with_confirmed_readback_stays_protected(self):
        self.school.submit.side_effect = ToolError('business_error', 'checkin_post', 4001)
        self.school.today.side_effect = [record(), record(), record('CHECKED_IN')]
        result = submit_checkin(SNAPSHOT, POINT, self.attempts, sleep=lambda _: None)
        self.assertTrue(result['confirmed'])
        self.assertFalse(result['retryAllowed'])
        self.school.today.side_effect = None
        self.school.today.return_value = record()
        second = submit_checkin(SNAPSHOT, POINT, self.attempts)
        self.assertEqual(second['result'], 'prior_attempt')
        self.school.submit.assert_called_once()

    def test_http_and_malformed_response_are_not_explicit_rejections(self):
        for reason in ('http_error', 'invalid_json', 'network_error', 'session_rejected'):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as directory:
                attempts = Attempts(Path(directory))
                self.school.submit.side_effect = ToolError(reason, 'checkin_post')
                self.school.submit.reset_mock()
                first = submit_checkin(SNAPSHOT, POINT, attempts, sleep=lambda _: None)
                self.assertEqual(first['result'], 'unknown')
                self.assertFalse(first['retryAllowed'])
                second = submit_checkin(SNAPSHOT, POINT, attempts)
                self.assertEqual(second['result'], 'prior_attempt')
                self.school.submit.assert_called_once()

    def test_school_message_redacts_snapshot_credentials(self):
        self.school.submit.side_effect = ToolError('business_error', 'checkin_post', 4001,
            school_message=SNAPSHOT['student_id']+' '+SNAPSHOT['cookies'][0]['value']+
                           ' token=private-token <b>请稍后重试</b>')
        result = submit_checkin(SNAPSHOT, POINT, self.attempts, sleep=lambda _: None)
        self.assertNotIn(SNAPSHOT['student_id'], result['schoolMessage'])
        self.assertNotIn(SNAPSHOT['cookies'][0]['value'], result['schoolMessage'])
        self.assertNotIn('private-token', result['schoolMessage'])
        self.assertNotIn('<b>', result['schoolMessage'])
        self.assertIn('请稍后重试', result['schoolMessage'])

    def test_confirmation_address_reused_without_duplicate_schedule_query(self):
        self.school.today.side_effect = [record(), record(), record('CHECKED_IN')]
        result = submit_checkin(SNAPSHOT, POINT, self.attempts, address='已确认地址', sleep=lambda _: None)
        self.assertTrue(result['confirmed'])
        self.school.reverse_geocode.assert_not_called()
        self.school.schedule.assert_called_once()
        self.assertEqual(self.school.submit.call_args.args[0]['reverseGeocode'], '已确认地址')

    def test_existing_school_record_skips_post(self):
        self.school.today.return_value = record('CHECKED_IN')
        result = submit_checkin(SNAPSHOT, POINT, self.attempts)
        self.assertEqual(result['result'], 'already_recorded')
        self.assertFalse(result['submitted'])
        self.school.submit.assert_not_called()

    def test_outside_window_and_fence_prevent_post(self):
        self.school.schedule.return_value = {**SCHEDULE, 'studentCheckinStartOffsetMinute': 0,
                                            'studentCheckinEndOffsetMinute': 30}
        with self.assertRaises(ToolError) as error:
            submit_checkin(SNAPSHOT, POINT, self.attempts)
        self.assertEqual(error.exception.reason, 'outside_checkin_window')
        self.school.schedule.return_value = SCHEDULE
        self.school.locations.return_value = [{'latitude': 0, 'longitude': 0, 'radiusMeter': 50}]
        with self.assertRaises(ToolError) as error:
            submit_checkin(SNAPSHOT, POINT, self.attempts)
        self.assertEqual(error.exception.reason, 'outside_allowed_location')
        self.school.submit.assert_not_called()

    def test_persistent_claim_is_exclusive(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            claimed = list(pool.map(lambda _: self.attempts.claim('synthetic-hash', DAY), range(4)))
        self.assertEqual(sum(claimed), 1)

    def test_rejected_claim_can_only_be_reclaimed_by_one_concurrent_request(self):
        self.attempts.claim('synthetic-hash', DAY)
        self.attempts.finish('synthetic-hash', DAY, 'rejected')
        with ThreadPoolExecutor(max_workers=4) as pool:
            claimed = list(pool.map(lambda _: self.attempts.claim('synthetic-hash', DAY), range(4)))
        self.assertEqual(sum(claimed), 1)
        self.assertEqual(self.attempts.outcome('synthetic-hash', DAY), 'started')

    def test_started_or_unrecognized_outcome_is_never_reclaimed(self):
        for outcome in ('started', 'unknown', 'confirmed', 'record_changed', 'future-outcome'):
            self.attempts.claim(outcome, DAY)
            self.attempts.finish(outcome, DAY, outcome)
            self.assertFalse(self.attempts.claim(outcome, DAY))


class TransportTests(unittest.TestCase):
    def test_post_is_exact_one_shot_capability(self):
        transport = SubmissionTransport(SNAPSHOT)
        with patch('core.Transport.request') as network:
            with self.assertRaises(ToolError):
                transport.request('POST', SUBMIT_URL, stage='checkin_post', payload={})
            transport.permit = True
            transport.request('POST', SUBMIT_URL, stage='checkin_post', payload={})
            with self.assertRaises(ToolError):
                transport.request('POST', SUBMIT_URL, stage='checkin_post', payload={})
            network.assert_called_once()

    def test_capability_does_not_allow_report(self):
        transport = SubmissionTransport(SNAPSHOT)
        transport.permit = True
        with patch('core.Transport.request') as network:
            with self.assertRaises(ToolError):
                transport.request('POST', SUBMIT_URL.replace('/checkin', '/report'), stage='checkin_post', payload={})
            network.assert_not_called()


class ConfirmationTests(WebAppTestCase):
    def setUp(self):
        super().setUp()
        self.clock_patch = patch('app.now', return_value=INSTANT)
        self.clock_patch.start()
        self.addCleanup(self.clock_patch.stop)
        self.store.save_session(SNAPSHOT)
        self.store.save_location(POINT)

    def ticket(self):
        with patch('app.prepare_checkin', return_value={'businessDate': DAY, 'submitted': False}):
            response = self.post('/api/checkin/prepare', {})
        self.assertEqual(response.status_code, 200)
        return response.json['confirmation']

    def test_confirmation_consumed_once_and_requires_both_checks(self):
        token = self.ticket()
        data = {'confirmation': token, 'confirm': True, 'actual_location_confirmed': True}
        with patch('app.submit_checkin', return_value={'confirmed': True, 'submitted': True}) as submit:
            self.assertEqual(self.post('/api/checkin', {**data, 'confirm': False}).status_code, 400)
            self.assertEqual(self.post('/api/checkin', {**data, 'actual_location_confirmed': False}).status_code, 400)
            self.assertEqual(self.post('/api/checkin', data).status_code, 200)
            self.assertEqual(self.post('/api/checkin', data).status_code, 400)
            submit.assert_called_once()

    def test_changed_location_invalidates_confirmation(self):
        token = self.ticket()
        self.store.save_location({**POINT, 'latitude': 30.8})
        with patch('app.submit_checkin') as submit:
            response = self.post('/api/checkin', {'confirmation': token, 'confirm': True, 'actual_location_confirmed': True})
            self.assertEqual(response.status_code, 400)
            submit.assert_not_called()

    def test_expired_confirmation_never_submits(self):
        token = self.ticket()
        self.workspace.pending[token]['expires'] = 0
        with patch('app.submit_checkin') as submit:
            response = self.post('/api/checkin', {'confirmation': token, 'confirm': True, 'actual_location_confirmed': True})
            self.assertEqual(response.status_code, 400)
            submit.assert_not_called()

    def test_rejection_requires_a_new_confirmation_before_manual_retry(self):
        school = MagicMock()
        school.today.return_value = record()
        school.locations.return_value = [{**normalize_location(POINT)['gcj02'], 'radiusMeter': 150}]
        school.schedule.return_value = SCHEDULE
        school.reverse_geocode.return_value = '合成测试地址'
        school.submit.side_effect = ToolError('business_error', 'checkin_post', 4001)
        data = {'confirmation': self.ticket(), 'confirm': True, 'actual_location_confirmed': True}
        with patch('checkin.school_for', return_value=school), patch('checkin.now', return_value=INSTANT):
            first = self.post('/api/checkin', data)
            self.assertEqual(first.json['result']['result'], 'rejected')
            self.assertEqual(self.post('/api/checkin', data).status_code, 400)
            school.submit.assert_called_once()
            data['confirmation'] = self.ticket()
            school.submit.side_effect = None
            school.today.side_effect = [record(), record(), record('CHECKED_IN')]
            second = self.post('/api/checkin', data)
            self.assertEqual(second.json['result']['result'], 'confirmed')
            self.assertEqual(school.submit.call_count, 2)
