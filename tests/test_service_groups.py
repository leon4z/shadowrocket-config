import copy
import unittest
from test_build import build, spec, daily_v2, upstream


def manifest():
    data = daily_v2()
    names = {a.split(' = ', 1)[0] for a in spec.ANCHORS if ' = select,' in a} - spec.STABLE_ONLY_SERVICES
    data.update(version=3, services={name: {'country':'日本', 'pattern':'(?i)^(?:NodeA|NodeB)$',
                                          'node_count':2, 'probe_level':'web_entry'} for name in names})
    return data


class ServiceGroupsTest(unittest.TestCase):
    def test_personal_fallback_gets_exact_service_subset_other_modes_unchanged(self):
        data = build.validate_selection(manifest(), spec)
        for variant in spec.VARIANTS:
            text = build.transform(upstream(), spec, variant, data)
            sections = build.parse_sections(text)
            build.validate_variant(sections, spec, variant, data, upstream())
            defs = dict(build.split_params(line) for line in build.effective(sections['proxy group']))
            if variant['audience']=='personal' and variant['mode']=='fallback':
                self.assertEqual(defs['TikTok'], ['select','TikTok精选'])
                self.assertEqual(defs['TikTok精选'][0], 'fallback')
                self.assertIn('policy-regex-filter=(?i)^(?:NodeA|NodeB)$', defs['TikTok精选'])
            else:
                self.assertNotIn('TikTok精选', defs)
            if variant['audience']=='generic':
                self.assertEqual(text, build.transform(upstream(), spec, variant, None))

    def test_bad_service_country_secret_fields_and_unbounded_patterns_rejected(self):
        for update in ({'country':'未知'}, {'pattern':'(?i)^(?:.*)$'}, {'pattern':'(?i)^(?:Other)$'},
                       {'node_count':0}, {'node_count':4}, {'password':'secret'}, {'probe_level':'app_verified'}):
            data = manifest(); data['services']['TikTok'].update(update)
            with self.subTest(update=update), self.assertRaises(build.Failure):
                build.validate_selection(data, spec)
        for name in ('AI', '谷歌服务','Unknown'):
            data=manifest(); data['services'][name]=data['services'].pop('TikTok')
            with self.assertRaises(build.Failure): build.validate_selection(data, spec)

    def test_missing_or_bypassed_service_group_fails_output_validation(self):
        data=manifest(); variant=next(v for v in spec.VARIANTS if v['id']=='fallback')
        lines=build.transform(upstream(),spec,variant,data)
        for broken in ([line for line in lines if not line.startswith('TikTok精选 =')],
                       [line.replace('TikTok = select,TikTok精选','TikTok = select,速度') for line in lines],
                       [line.replace('TikTok精选 = fallback','TikTok精选 = url-test') for line in lines]):
            with self.assertRaises(build.Failure):
                build.validate_variant(build.parse_sections(broken),spec,variant,data,upstream())
