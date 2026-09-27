import copy
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_build import build, spec, daily_v2, upstream


class OrderedStableTest(unittest.TestCase):
    def test_generated_headers_identify_fixed_priority_only_in_personal_stable_groups(self):
        fixture = upstream().replace('FINAL,PROXY', '\n'.join(
            [f"RULE-SET,{entry['qx']},PROXY" for entry in spec.RULESET_DIALECT] + ['FINAL,PROXY']))
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(build, 'ROOT', directory), \
             patch.object(build, 'load_selection', return_value=daily_v2()), \
             patch.object(build, 'fetch', return_value=fixture), \
             patch.object(build, 'check_ruleset', return_value={'rules': 1, 'ip': 0, 'ip_no_resolve': 0}), \
             patch.object(build, 'check_domainset', return_value={'domains': 1}), \
             patch('sys.argv', ['build.py']), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(build.main(), 0)
            for variant in spec.VARIANTS:
                text = (Path(directory) / 'dist' / (variant['id'] + '.conf')).read_text()
                self.assertEqual('完整备注固定顺序' in text,
                                 variant['audience'] == 'personal' and variant['strict_stable'])

    def test_personal_modes_keep_residential_priority_across_daily_selections(self):
        expected = [
            '美国 Grande&RCN 66.167.174.200 · 主线',
            '美国 Grande&RCN 66.167.174.200 · 备用',
            'BZ-VMess-TLS',
        ]
        for selection in (daily_v2(), daily_v2(('日本节点',), '日本')):
            for variant in spec.VARIANTS:
                with self.subTest(mode=variant['id'], country=selection['speed_country']):
                    lines = build.transform(upstream(), spec, variant, selection)
                    sections = build.parse_sections(lines)
                    build.validate_variant(sections, spec, variant, selection, upstream())
                    definitions = dict(build.split_params(line) for line in build.effective(sections['proxy group']))
                    if variant['audience'] == 'personal' and variant['strict_stable']:
                        self.assertEqual(definitions['稳定'], ['fallback', *expected,
                            'interval=600', 'timeout=5', 'url=https://www.gstatic.com/generate_204'])
                    elif variant['strict_stable']:
                        self.assertIn('policy-regex-filter=(?!)', definitions['稳定'])
                        self.assertFalse(any(name in '\n'.join(lines) for name in expected))
                    else:
                        self.assertNotIn('稳定', definitions)
                    self.assertFalse(build.effective(sections['proxy']))

    def test_changed_order_unknown_member_and_direct_escape_are_rejected(self):
        selection = daily_v2()
        for variant in spec.VARIANTS:
            if variant['audience'] != 'personal' or not variant['strict_stable']:
                continue
            original = build.parse_sections(build.transform(upstream(), spec, variant, selection))
            for change in ('reorder', 'unknown', 'direct', 'other_group'):
                with self.subTest(mode=variant['id'], change=change), self.assertRaises(build.Failure):
                    broken = copy.deepcopy(original)
                    lines = broken['proxy group']
                    for i, line in enumerate(lines):
                        if change == 'other_group' and line.startswith('AI = '):
                            lines[i] = line + ',' + spec.STABLE_MEMBERS[0]
                        elif change != 'other_group' and line.startswith('稳定 = '):
                            name, params = build.split_params(line)
                            if change == 'reorder':
                                params[1], params[3] = params[3], params[1]
                            elif change == 'unknown':
                                params[1] = 'Unknown'
                            else:
                                params.append('DIRECT')
                            lines[i] = build.join_params(name, params)
                    build.validate_variant(broken, spec, variant, selection, upstream())

    def test_fixed_member_validation_and_speed_ip_protection(self):
        variant = next(v for v in spec.VARIANTS if v['id'] == 'fallback')
        for members in ((), (*spec.STABLE_MEMBERS[:2], spec.STABLE_MEMBERS[0]),
                        ('x=y', *spec.STABLE_MEMBERS[1:]),
                        ('DIRECT', *spec.STABLE_MEMBERS[1:]),
                        ('美国 Grande&RCN 66.167.174.201 · 主线', *spec.STABLE_MEMBERS[1:])):
            with self.subTest(members=members), patch.object(spec, 'STABLE_MEMBERS', members), self.assertRaises(build.Failure):
                build.ordered_stable_members(spec, variant)
        for name in spec.STABLE_MEMBERS[:2]:
            with self.subTest(name=name), self.assertRaises(build.Failure):
                build.validate_member_name(name, set())
