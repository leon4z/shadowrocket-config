import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_build import ROOT, build, selection, spec, upstream

renderer_spec = importlib.util.spec_from_file_location('karing_under_test', ROOT / 'scripts/render_karing.py')
renderer = importlib.util.module_from_spec(renderer_spec)
renderer_spec.loader.exec_module(renderer)


def entry(rule, **scope):
    return {'rule': rule, 'note': '测试规则用途', **scope}


class CustomRulesTest(unittest.TestCase):
    def test_custom_domains_precede_external_rules_in_every_variant_and_other_rules_are_preserved(self):
        rules = build.load_custom_rules(spec)
        expected = [
            'DOMAIN-SUFFIX,diabrowser.engineering,AI',
            'DOMAIN-SUFFIX,claude.dev,AI',
            'DOMAIN-SUFFIX,cursor.com,AI',
            'DOMAIN-SUFFIX,cursor.sh,AI',
            'DOMAIN-SUFFIX,cursorapi.com,AI',
            'DOMAIN-SUFFIX,cursor-cdn.com,AI',
            'DOMAIN-SUFFIX,cursorvm.com,AI',
        ]
        fixture = upstream().replace('FINAL,PROXY',
            'RULE-SET,https://example.com/Global.list,PROXY\nFINAL,PROXY')
        for variant in spec.VARIANTS:
            with self.subTest(variant=variant['id']):
                manifest = selection() if variant['audience'] == 'personal' else None
                baseline = build.transform(fixture, spec, variant, manifest)
                lines = build.transform(fixture, spec, variant, manifest, rules)
                content = build.parse_sections(lines)
                groups = build.validate_variant(content, spec, variant, manifest, fixture)
                build.validate_rules(content, groups)
                active = build.effective(content['rule'])
                self.assertEqual(active[:len(expected)], expected)
                start, end = lines.index(build.CUSTOM_RULES_START), lines.index(build.CUSTOM_RULES_END)
                self.assertEqual(lines[:start] + lines[end + 2:], baseline)
                self.assertEqual(lines, build.transform(fixture, spec, variant, manifest, rules))

    def test_explicit_proxy_is_not_replaced_by_mode_default(self):
        rules = build.validate_custom_rules([
            entry('DOMAIN,manual.example.com,PROXY', modes=['select', 'fallback', 'hybrid']),
            entry('DOMAIN-SUFFIX,local.example.com,DIRECT'),
        ], spec)
        for variant in spec.VARIANTS:
            manifest = selection() if variant['audience'] == 'personal' else None
            content = build.parse_sections(build.transform(upstream(), spec, variant, manifest, rules))
            groups = build.validate_variant(content, spec, variant, manifest, upstream())
            build.validate_rules(content, groups)
            active = build.effective(content['rule'])
            self.assertIn('DOMAIN-SUFFIX,local.example.com,DIRECT', active)
            self.assertEqual('DOMAIN,manual.example.com,PROXY' in active, variant['mode'] != 'stable')
            self.assertIn('FINAL,' + variant['default_policy'], active)

    def test_scoped_speed_and_stable_rules_only_appear_where_groups_exist(self):
        rules = build.validate_custom_rules([
            entry('DOMAIN,fast.example.com,速度', modes=['fallback'], audiences=['personal']),
            entry('DOMAIN-SUFFIX,fixed.example.com,稳定', modes=['fallback', 'hybrid', 'stable']),
        ], spec)
        for variant in spec.VARIANTS:
            active = build.effective(build.render_custom_rules(rules, variant))
            self.assertEqual('DOMAIN,fast.example.com,速度' in active, variant['id'] == 'fallback')
            self.assertEqual('DOMAIN-SUFFIX,fixed.example.com,稳定' in active, variant['strict_stable'])
        for policy in ['速度', '稳定', 'PROXY']:
            with self.subTest(policy=policy), self.assertRaises(build.Failure):
                build.validate_custom_rules([entry('DOMAIN,x.example.com,' + policy)], spec)

    def test_duplicate_conditions_are_rejected_only_for_overlapping_scopes(self):
        first = entry('DOMAIN-SUFFIX,EXAMPLE.COM,AI', modes=['fallback'])
        for policy in ['AI', 'DIRECT']:
            with self.subTest(policy=policy), self.assertRaisesRegex(build.Failure, '重复或出口冲突'):
                build.validate_custom_rules([first, entry('DOMAIN-SUFFIX,example.com,' + policy)], spec)
        rules = build.validate_custom_rules([first,
            entry('DOMAIN-SUFFIX,example.com,DIRECT', modes=['select']),
            entry('DOMAIN,exception.example.com,DIRECT'),
        ], spec)
        self.assertEqual(len(rules), 3)

    def test_specific_exceptions_keep_source_order(self):
        rules = build.validate_custom_rules([
            entry('DOMAIN,exception.example.com,DIRECT'),
            entry('DOMAIN-SUFFIX,example.com,AI'),
        ], spec)
        active = build.effective(build.render_custom_rules(rules, spec.VARIANTS[0]))
        self.assertEqual(active, [r['rule'] for r in rules])

    def test_invalid_input_and_missing_file_cannot_silently_remove_overrides(self):
        valid = entry('DOMAIN,x.example.com,AI')
        bad = [None, {}, [dict(valid, unknown=True)], [dict(valid, note='')],
               [dict(valid, note='two\nlines')], [dict(valid, rule='DOMAIN,x.example.com,AI\nFINAL,DIRECT')],
               [dict(valid, rule='FINAL,DIRECT')], [dict(valid, rule='DOMAIN,*.example.com,AI')],
               [dict(valid, rule='DOMAIN,https://example.com,AI')],
               [dict(valid, rule='DOMAIN,x.example.com,Missing')],
               [dict(valid, modes=[])], [dict(valid, modes=['bad'])],
               [dict(valid, modes=['fallback', 'fallback'])], [dict(valid, audiences='personal')],
               [dict(valid, modes=['stable'], audiences=['generic'])]]
        for document in bad:
            with self.subTest(document=document), self.assertRaises(build.Failure):
                build.validate_custom_rules(document, spec)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'missing.json'
            with patch.object(build, 'CUSTOM_RULES_PATH', str(path)):
                with self.assertRaises(build.Failure):
                    build.load_custom_rules(spec)
                path.write_text('{broken')
                with self.assertRaises(build.Failure):
                    build.load_custom_rules(spec)
                path.write_text('[]')
                self.assertEqual(build.load_custom_rules(spec), [])

    def test_karing_preserves_override_order_and_explicit_outbounds(self):
        rules = build.validate_custom_rules([
            entry('DOMAIN,exception.diabrowser.engineering,DIRECT'),
            entry('DOMAIN-SUFFIX,diabrowser.engineering,AI'),
            entry('DOMAIN-WILDCARD,*.manual.example.com,PROXY', modes=['fallback']),
        ], spec)
        variant = next(v for v in spec.VARIANTS if v['id'] == 'fallback')
        fixture = upstream().replace('FINAL,PROXY',
            'RULE-SET,https://example.com/Global.list,PROXY\nFINAL,PROXY')
        conf = '\n'.join(build.transform(fixture, spec, variant, selection(), rules))
        entries = renderer.parse_rule_lines(conf)
        self.assertEqual([e[2] for e in entries[:3]], ['DIRECT', '稳定', 'PROXY'])
        with tempfile.TemporaryDirectory() as tmp:
            source, out = Path(tmp) / 'input.conf', Path(tmp) / 'output'
            source.write_text(conf)
            with patch('sys.argv', ['render_karing.py', '--conf', str(source), '--out-dir', str(out)]), \
                 patch.object(renderer, 'fetch', return_value='DOMAIN-SUFFIX,example.com'), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(renderer.main(), 0)
            rendered = json.loads((out / 'diversion_rules_custom.json').read_text())['rules']
            self.assertEqual([r['outbound'] for r in rendered[:3]],
                             ['direct', 'currentSelected', 'currentSelected'])
            self.assertEqual(rendered[3]['outbound'], 'direct')
            suffix = json.loads((out / 'ruleset/Custom-002.json').read_text())
            self.assertEqual(suffix['rules'], [{'domain_suffix': ['diabrowser.engineering']}])


if __name__ == '__main__':
    unittest.main()
