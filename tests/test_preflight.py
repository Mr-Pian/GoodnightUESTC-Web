"""Fail before configuration/downloads on unsupported or inaccessible Docker tools."""
import json
import subprocess
import unittest
from unittest.mock import patch

from deploy import preflight


class PreflightTests(unittest.TestCase):
    def check(self, *, engine='29.1.3', compose='2.40.3', buildx='github.com/docker/buildx v0.29.1',
              architecture='amd64', build=True, network=False, fail=None):
        def execute(command, **kwargs):
            if command[1:3] == ['version', '--format']:
                output = json.dumps({'Client': {'Version': engine}, 'Server': {
                    'Version': engine, 'Os': 'linux', 'Arch': architecture}})
            elif command[1:3] == ['compose', 'version']:
                output = compose
            elif command[1:3] == ['buildx', 'version']:
                output = buildx
            else:
                output = 'Driver: docker'
            if fail and command[1:3] == fail:
                raise subprocess.CalledProcessError(1, command, stderr='private daemon details')
            return subprocess.CompletedProcess(command, 0, output)
        with patch.object(preflight.subprocess, 'run', side_effect=execute) as run, \
                patch.dict('os.environ', {'DOCKER_BUILDKIT': '1'}):
            result = preflight.check_tools(network, build=build)
        return result, run.call_args_list

    def test_local_checks_have_timeouts_and_do_not_pull_or_build(self):
        result, calls = self.check()
        self.assertEqual(len(result), 3)
        for call in calls:
            self.assertEqual(call.kwargs['timeout'], 20)
            self.assertNotIn('pull', call.args[0])
            self.assertNotIn('build', call.args[0])
            self.assertNotIn('--bootstrap', call.args[0])

    def test_rejects_old_engine_compose_and_buildx(self):
        for options in ({'engine':'23.0.0'}, {'compose':'2.19.9'}, {'buildx':'buildx v0.11.2'},
                        {'compose':'2.24.3','network':True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.check(**options)

    def test_missing_buildx_and_unreachable_builder_have_actionable_sanitized_errors(self):
        for command, expected in ((['buildx','version'], 'docker-buildx-plugin'),
                                  (['buildx','inspect'], 'builder'),
                                  (['version','--format'], 'daemon')):
            with self.subTest(command=command), self.assertRaisesRegex(ValueError, expected) as error:
                self.check(fail=command)
            self.assertNotIn('private daemon details', str(error.exception))

    def test_image_runtime_does_not_need_buildx_and_checks_platform(self):
        _, calls = self.check(build=False, fail=['buildx','version'])
        self.assertFalse(any('buildx' in call.args[0] for call in calls))
        with self.assertRaisesRegex(ValueError, 'amd64'):
            self.check(build=False, architecture='arm64')

    def test_docker_timeout_is_reported_without_raw_output(self):
        with patch.object(preflight.subprocess, 'run', side_effect=subprocess.TimeoutExpired(['docker'],20)), \
                self.assertRaisesRegex(ValueError, 'daemon'):
            preflight.check_tools()


if __name__ == '__main__':
    unittest.main()
