import copy
import re
import unittest
from test_build import build, spec, daily_v2, upstream


def manifest():
    data = daily_v2()
    data.update(version=4, services={}, speed_order=['NodeC', 'NodeA', 'NodeB'])
    return data


class OrderedSpeedTest(unittest.TestCase):
    def test_only_personal_fallback_gets_ordered_external_members(self):
        data = build.validate_selection(manifest(), spec)
        for variant in spec.VARIANTS:
            lines = build.transform(upstream(), spec, variant, data)
            sections = build.parse_sections(lines)
            build.validate_variant(sections, spec, variant, data, upstream())
            definitions = dict(build.split_params(line) for line in build.effective(sections['proxy group']))
            if variant['audience'] == 'personal' and variant['mode'] == 'fallback':
                self.assertEqual(definitions['速度'][1:4], data['speed_order'])
                self.assertNotIn('policy-regex-filter', ','.join(definitions['速度']))
                self.assertNotIn('DIRECT', definitions['速度'])
            else:
                old = daily_v2() if variant['audience'] == 'personal' else None
                self.assertEqual(lines, build.transform(upstream(), spec, variant, old))

    def test_invalid_order_and_private_fields_rejected(self):
        for changes in ({'speed_order':['NodeA']}, {'speed_order':['NodeA','NodeB','Other']},
                        {'speed_order':['NodeA','NodeB','NodeB']}, {'speed_country':None},
                        {'server':'private'}, {'services':None}):
            data = manifest(); data.update(changes)
            with self.subTest(changes=changes), self.assertRaises(build.Failure):
                build.validate_selection(data, spec)
        for name in ('DIRECT','稳定','YouTube','x=y','x#y','x,y',' x ',
                     '1.2.3.4', '[2001:db8::1]', 'https://x.example'):
            data=manifest()
            pattern='(?i)^(?:'+ '|'.join(re.escape(n).replace(',', r'\x2c').replace('#', r'\x23') for n in [name,'NodeB','NodeC']) + ')$'
            data['groups']['速度']['pattern']=pattern
            data['groups']['香港节点']['pattern']=pattern
            data['speed_order']=[name,'NodeB','NodeC']
            with self.subTest(name=name), self.assertRaises(build.Failure):
                build.validate_selection(data, spec)

    def test_name_conversion_and_collision(self):
        data=manifest()
        names=['香港-HK-1-流量倍率:0.7','NodeB','NodeC']
        pattern='(?i)^(?:'+ '|'.join(re.escape(n) for n in names) + ')$'
        data['groups']['速度']['pattern']=pattern
        data['groups']['香港节点']['pattern']=pattern
        data['speed_order']=names
        build.validate_selection(data,spec)
        variant=next(v for v in spec.VARIANTS if v['id']=='fallback')
        self.assertEqual(build.ordered_speed_members(data,variant)[0], '香港-HK-1-:0.7')
        data['groups']['速度']['pattern']='(?i)^(?:x流量倍率|x|NodeC)$'
        data['groups']['香港节点']['pattern']=data['groups']['速度']['pattern']
        data['speed_order']=['x流量倍率','x','NodeC']
        with self.assertRaises(build.Failure): build.validate_selection(data,spec)

    def test_output_cannot_reorder_or_add_unknown_references(self):
        data=manifest(); variant=next(v for v in spec.VARIANTS if v['id']=='fallback')
        lines=build.transform(upstream(),spec,variant,data)
        for old,new in [('NodeC,NodeA,NodeB','NodeA,NodeC,NodeB'),
                        ('NodeC,NodeA,NodeB','NodeC,NodeA,Unknown'),
                        ('YouTube = select,速度','YouTube = select,NodeA')]:
            broken=[line.replace(old,new) for line in lines]
            with self.assertRaises(build.Failure):
                build.validate_variant(build.parse_sections(broken),spec,variant,data,upstream())
