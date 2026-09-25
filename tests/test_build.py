import importlib.util
import contextlib
import io
from pathlib import Path
import re
import tempfile
import unittest
import http.client
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec_file = importlib.util.spec_from_file_location("build_under_test", ROOT / "scripts" / "build.py")
build = importlib.util.module_from_spec(spec_file)
spec_file.loader.exec_module(build)
spec = build.load_spec()


def selection():
    groups = {name: {"pattern": "(?i)^(?:NodeA|NodeB)$", "node_count": 2}
              for name in spec.REQUIRED_SAMPLED_GROUPS}
    groups["澳大利亚节点"] = {"pattern": "(?i)^(?:Sydney\\x2c01)$", "node_count": 1}
    return {"version": 1, "mode": "trial", "generated_at": 1800000900,
            "window_start": 1800000000, "window_end": 1800000800,
            "completed_runs": 3, "selection_id": "trial_abc123", "groups": groups}


def upstream():
    services = [anchor for anchor in spec.ANCHORS if " = select," in anchor]
    countries = [f"{name} = url-test,url=http://www.gstatic.com/generate_204,"
                 "interval=600,tolerance=0,timeout=5,policy-regex-filter=US|SG"
                 for name in sorted(spec.REQUIRED_SAMPLED_GROUPS - {"速度"})]
    return "\n".join(["[General]", "ipv6 = true", "", "[Proxy]", "# Only comments", "",
                       "[Proxy Group]", *services, *countries, "", "[Rule]",
                       "DOMAIN-SUFFIX,ai.example,AI", "DOMAIN-SUFFIX,cn.example,DIRECT",
                       "FINAL,PROXY", ""])


def daily_v2(countries=("香港节点", "日本节点"), speed_country="香港"):
    manifest = selection()
    manifest.update(version=2, mode='rolling24h', window_start=1800000000,
                    window_end=1800021600, generated_at=1800021700, completed_runs=18,
                    upstream_commit='a'*40, upstream_run_id=123, speed_country=speed_country)
    manifest['groups'] = {name: {'pattern':'(?i)^(?:NodeA|NodeB|NodeC)$','node_count':3}
                          for name in ('速度', *countries)}
    return manifest


def sections(variant):
    manifest = build.validate_selection(selection(), spec)
    return build.parse_sections(build.transform(upstream(), spec, variant, manifest))


class BuildTest(unittest.TestCase):
    def test_personal_filters_accept_only_the_two_traffic_label_spellings(self):
        names = ('日本-TY-4-HY2-流量倍率:0.6', '日本-TY-5-HY2-流量倍率:1')
        other = '🇺🇸 [Hy2]US 01'
        source_pattern = '(?i)^(?:' + '|'.join(re.escape(name) for name in (*names, other)) + ')$'
        manifest = daily_v2(('日本节点', '美国节点'), None)
        manifest['groups']['速度'] = {'pattern': source_pattern, 'node_count': 3}
        manifest['groups']['日本节点'] = {'pattern': '(?i)^(?:' + '|'.join(re.escape(name) for name in names) + ')$',
                                        'node_count': 2}
        manifest['groups']['美国节点'] = {'pattern': '(?i)^(?:' + re.escape(other) + ')$', 'node_count': 1}
        manifest['version'] = 3
        manifest['services'] = {'YouTube': {'country': '日本',
            'pattern': manifest['groups']['日本节点']['pattern'], 'node_count': 2,
            'probe_level': 'web_entry'}}
        build.validate_selection(manifest, spec)
        variant = next(v for v in spec.VARIANTS if v['id'] == 'fallback')
        content = build.parse_sections(build.transform(upstream(), spec, variant, manifest))
        build.validate_variant(content, spec, variant, manifest, upstream())
        definitions = dict(build.split_params(line) for line in build.effective(content['proxy group']))
        for group in ('速度', '日本节点', 'YouTube精选'):
            pattern = next(value.split('=', 1)[1] for value in definitions[group]
                           if value.startswith('policy-regex-filter='))
            for name in names:
                self.assertIsNotNone(re.fullmatch(pattern, name))
                self.assertIsNotNone(re.fullmatch(pattern, name.replace('流量倍率', '')))
                self.assertIsNone(re.fullmatch(pattern, name.replace('流量倍率', '流量')))
        self.assertEqual(manifest['services']['YouTube']['pattern'],
                         manifest['groups']['日本节点']['pattern'])
        self.assertEqual(build.shadowrocket_name_pattern('(?i)^(?:NodeA)$'), '(?i)^(?:NodeA)$')
        for other_variant in spec.VARIANTS:
            with self.subTest(variant=other_variant['id']):
                lines = build.transform(upstream(), spec, other_variant, manifest)
                if other_variant['audience'] == 'generic' or other_variant['mode'] == 'stable':
                    self.assertEqual(lines, build.transform(upstream(), spec, other_variant, None))
                    continue
                rendered = dict(build.split_params(line) for line in build.effective(
                    build.parse_sections(lines)['proxy group']))
                self.assertEqual('YouTube精选' in rendered, other_variant['mode'] == 'fallback')
                country_pattern = next(value.split('=', 1)[1] for value in rendered['日本节点']
                                       if value.startswith('policy-regex-filter='))
                self.assertTrue(all(re.fullmatch(country_pattern, name.replace('流量倍率', ''))
                                    for name in names))

    def test_daily_manifest_pins_upstream_and_enforces_freshness(self):
        manifest = selection()
        manifest.update(mode='rolling24h', completed_runs=60, window_start=1800000000,
                        window_end=1800085800, generated_at=1800086400,
                        upstream_commit='a'*40, upstream_run_id=123)
        build.validate_selection(manifest, spec)
        self.assertIn('/'+'a'*40+'/', build.upstream_url(spec, manifest))
        build.check_selection_freshness(manifest, now=1800086500)
        for at in (1800094001, 1800085000):
            with self.assertRaises(build.Failure):
                build.check_selection_freshness(manifest, now=at)
        for change in ({'upstream_commit':'../release'}, {'completed_runs':3},
                       {'window_start':1800085000}):
            with self.assertRaises(build.Failure):
                build.validate_selection({**manifest, **change}, spec)
        self.assertEqual(build.upstream_url(spec, None), spec.UPSTREAM_URL)

    def test_truncated_download_is_retried(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self): return b'complete-body'
        with patch.object(build.urllib.request, 'urlopen', side_effect=[http.client.IncompleteRead(b'part'), Response()]) as fetch, \
             patch.object(build.time, 'sleep'):
            self.assertEqual(build.fetch('https://example.invalid/rules'), 'complete-body')
            self.assertEqual(fetch.call_count, 2)

    def test_seven_variants_route_as_requested_and_use_separate_pools(self):
        self.assertEqual([v["id"] for v in spec.VARIANTS], [
            "Shadowrocket-select", "Shadowrocket-fallback", "Shadowrocket-hybrid",
            "select", "fallback", "hybrid", "stable"])
        for variant in spec.VARIANTS:
            with self.subTest(variant=variant["id"]):
                content = sections(variant)
                groups = build.validate_variant(content, spec, variant, selection(), upstream())
                build.validate_rules(content, groups)
                definitions = dict(build.split_params(line) for line in build.effective(content["proxy group"]))
                target = variant["default_policy"]
                self.assertIn(target, definitions["YouTube"])
                self.assertEqual(definitions["Spotify"][1], "DIRECT")
                self.assertIn("DOMAIN-SUFFIX,cn.example,DIRECT", content["rule"])
                self.assertIn(f"FINAL,{target}", content["rule"])
                expected_special = {"select": set(), "hybrid": {"稳定"},
                                    "fallback": {"速度", "稳定"}, "stable": {"稳定"}}[variant["mode"]]
                self.assertEqual(set(definitions) & {"速度", "稳定"}, expected_special)
                for name in expected_special:
                    self.assertEqual(definitions[name][0], "fallback")
                    self.assertFalse(any(p.startswith("tolerance=") for p in definitions[name]))
                for name, params in definitions.items():
                    if name.endswith("节点"):
                        self.assertEqual(params[0], "url-test")
                        self.assertIn("tolerance=100", params)
                if variant["mode"] == "stable":
                    self.assertFalse(any(name.endswith("节点") for name in definitions))
                    self.assertEqual(definitions["YouTube"], ["select", "稳定"])
                    self.assertEqual(definitions["Spotify"], ["select", "DIRECT", "稳定"])
                elif variant["audience"] == "personal":
                    self.assertEqual(definitions["澳大利亚节点"][0], "url-test")
                    self.assertIn("tolerance=100", definitions["澳大利亚节点"])
                    self.assertIn("url=https://www.gstatic.com/generate_204", definitions["澳大利亚节点"])
                    self.assertEqual(definitions["澳大利亚节点"][1],
                                     "policy-regex-filter=(?i)^(?:Sydney\\x2c01)$")
                    if "稳定" in definitions:
                        self.assertIn(f"policy-regex-filter={spec.STABLE_PATTERN}", definitions["稳定"])
                else:
                    self.assertNotIn("澳大利亚节点", definitions)
                    self.assertIn("policy-regex-filter=US|SG", definitions["美国节点"])
                    if "稳定" in definitions:
                        self.assertIn("policy-regex-filter=(?!)", definitions["稳定"])
                self.assertFalse(build.effective(content["proxy"]))
                self.assertFalse(any(line.split("=", 1)[0].strip().lower() == "update-url"
                                     for line in build.effective(content["general"])))
                if variant["strict_stable"]:
                    self.assertEqual(definitions["AI"], ["select", "稳定"])
                    self.assertEqual(definitions["谷歌服务"], ["select", "稳定"])
                else:
                    self.assertIn("PROXY", definitions["AI"])
                    self.assertIn("PROXY", definitions["谷歌服务"])

    def test_all_variants_remove_update_url_from_upstream_and_shared_overrides(self):
        for key in ("update-url", "UPDATE-URL"):
            fixture = upstream().replace("[General]", f"[General]\n{key} = https://example.com/old.conf")
            shared = {**spec.GENERAL_OVERRIDES, key: "https://example.com/shared.conf"}
            with patch.object(spec, "GENERAL_OVERRIDES", shared):
                for variant in spec.VARIANTS:
                    with self.subTest(key=key, variant=variant["id"]):
                        manifest = selection() if variant["audience"] == "personal" else None
                        result = build.transform(fixture, spec, variant, manifest)
                        self.assertEqual(result, build.transform(upstream(), spec, variant, manifest))
                        self.assertFalse(any("update-url" in line.lower() for line in result))
                        build.validate_variant(build.parse_sections(result), spec, variant, manifest, fixture)

    def test_update_url_validation_rejects_injected_field_in_every_variant(self):
        for variant in spec.VARIANTS:
            for key in ("update-url", "UPDATE-URL"):
                with self.subTest(variant=variant["id"], key=key):
                    content = sections(variant)
                    content["general"].append(f"{key} = https://example.com/unwanted.conf")
                    with self.assertRaisesRegex(build.Failure, "不得包含 update-url"):
                        build.validate_variant(content, spec, variant, selection(), upstream())

    def test_manifest_requires_exact_literal_nonempty_filters(self):
        bad_entries = [
            {"pattern": "(?i)^(?:US.*)$", "node_count": 1},
            {"pattern": "US|SG", "node_count": 1},
            {"pattern": "(?i)^(?:)$", "node_count": 1},
            {"pattern": "(?i)^(?:foo,bar)$", "node_count": 1},
            {"pattern": "(?i)^(?:foo\nbar)$", "node_count": 1},
            {"pattern": "(?i)^(?:foo|)$", "node_count": 1},
            {"pattern": "(?i)^(?:foo)$", "node_count": 0},
        ]
        for entry in bad_entries:
            with self.subTest(entry=entry):
                manifest = selection()
                manifest["groups"]["速度"] = entry
                with self.assertRaises(build.Failure):
                    build.validate_selection(manifest, spec)
        manifest = selection()
        del manifest["groups"]["速度"]
        with self.assertRaises(build.Failure):
            build.validate_selection(manifest, spec)
        manifest = selection()
        manifest["groups"]["ANY"] = {"pattern": "(?i)^(?:X)$", "node_count": 1}
        with self.assertRaises(build.Failure):
            build.validate_selection(manifest, spec)

    def test_missing_selection_does_not_fall_back_to_all_nodes(self):
        with self.assertRaises(build.Failure):
            build.load_selection(spec, str(ROOT / "tests" / "missing-selection.json"))

    def test_generic_ignores_personal_data_and_empty_stable_matches_nothing(self):
        for variant in spec.VARIANTS[:3]:
            without = build.transform(upstream(), spec, variant, None)
            self.assertEqual(without, build.transform(upstream(), spec, variant, selection()))
            self.assertNotIn("NodeA", "\n".join(without))
            self.assertNotIn(spec.STABLE_PATTERN, "\n".join(without))
            for name in ("", "US server", "NodeA", "稳定节点", "BZ-VMess-TLS"):
                self.assertIsNone(re.search(spec.GENERIC_STABLE_PATTERN, name))
            build.validate_variant(build.parse_sections(without), spec, variant, None, upstream())
        for variant in (v for v in spec.VARIANTS if v['audience'] == 'personal' and v['mode'] != 'stable'):
            with self.assertRaises(build.Failure):
                build.transform(upstream(), spec, variant, None)
        stable = next(v for v in spec.VARIANTS if v['mode'] == 'stable')
        self.assertEqual(build.transform(upstream(), spec, stable, None),
                         build.transform(upstream(), spec, stable, selection()))

    def test_generic_cli_does_not_load_selection_and_writes_only_three_files(self):
        # Complete synthetic anchors, including the four rule-set replacements.
        fixture = upstream().replace("FINAL,PROXY", "\n".join(
            [f"RULE-SET,{d['qx']},PROXY" for d in spec.RULESET_DIALECT] + ["FINAL,PROXY"]))
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(build, "ROOT", directory), \
             patch.object(build, "load_selection", side_effect=AssertionError("must not load personal data")), \
             patch.object(build, "fetch", return_value=fixture), \
             patch.object(build, "check_ruleset", return_value={"rules": 1, "ip": 0, "ip_no_resolve": 0}), \
             patch.object(build, "check_domainset", return_value={"domains": 1}), \
             patch("sys.argv", ["build.py", "--audience", "generic"]), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(build.main(), 0)
            outputs = list((Path(directory) / "dist").glob("*.conf"))
            self.assertEqual(len(outputs), 3)
            for output in outputs:
                self.assertTrue(output.name.startswith("Shadowrocket-"))
                self.assertNotIn("试跑快照", output.read_text())

    def test_speed_limit_ten_does_not_cap_country_groups(self):
        manifest = selection()
        manifest["groups"]["速度"]["node_count"] = 10
        manifest["groups"]["香港节点"]["node_count"] = 50
        build.validate_selection(manifest, spec)
        manifest["groups"]["速度"]["node_count"] = 11
        with self.assertRaises(build.Failure):
            build.validate_selection(manifest, spec)

    def test_new_upstream_region_is_generic_and_omitted_without_personal_samples(self):
        changed = upstream().replace("[Rule]", "加拿大节点 = url-test,policy-regex-filter=Canada\n[Rule]")
        generic, personal = spec.VARIANTS[0], spec.VARIANTS[3]
        output = build.transform(changed, spec, generic, None)
        self.assertIn("加拿大节点", build.validate_variant(build.parse_sections(output), spec, generic, None, changed))
        with self.assertRaises(build.Failure):
            build.transform(changed, spec, personal, selection())
        output = build.transform(changed, spec, personal, daily_v2())
        self.assertNotIn("加拿大节点", build.validate_variant(build.parse_sections(output),spec,personal,daily_v2(),changed))

    def test_v2_minimum_history_and_country_metadata(self):
        manifest=daily_v2()
        build.validate_selection(manifest,spec)
        for change in ({'completed_runs':17},{'window_end':1800021599},{'speed_country':'美国'},
                       {'speed_country':42},{'version':True},{'mode':'trial'}):
            with self.subTest(change=change),self.assertRaises(build.Failure):
                build.validate_selection({**manifest,**change},spec)
        build.validate_selection(daily_v2((),None),spec)
        manifest['groups']['速度']['pattern']='(?i)^(?:AnotherNode)$'
        with self.assertRaisesRegex(build.Failure,'主国家'):
            build.validate_selection(manifest,spec)

    def test_sparse_personal_countries_remove_only_unavailable_menu_choices(self):
        fixture=upstream().replace('YouTube = select,PROXY','YouTube = select,PROXY,美国节点,香港节点')
        manifest=build.validate_selection(daily_v2(),spec)
        for variant in spec.VARIANTS:
            with self.subTest(variant=variant['id']):
                lines=build.transform(fixture,spec,variant,manifest)
                content=build.parse_sections(lines)
                groups=build.validate_variant(content,spec,variant,manifest,fixture)
                build.validate_rules(content,groups)
                if variant['audience']=='generic':
                    self.assertEqual(lines,build.transform(fixture,spec,variant,None))
                    self.assertIn('美国节点',groups)
                else:
                    if variant['mode'] == 'stable':
                        self.assertFalse(any(g.endswith('节点') for g in groups))
                        self.assertEqual(build.split_params(next(l for l in lines if l.startswith('YouTube = ')))[1],
                                         ['select', '稳定'])
                    else:
                        self.assertEqual({g for g in groups if g.endswith('节点')},{'香港节点','日本节点'})
                        self.assertNotIn('美国节点','\n'.join(lines))
                        self.assertIn('香港节点 = url-test','\n'.join(lines))

    def test_zero_existing_or_all_country_groups_can_be_omitted(self):
        for countries,country in [((),None),(('加拿大节点',),'加拿大')]:
            manifest=build.validate_selection(daily_v2(countries,country),spec)
            for variant in spec.VARIANTS[3:]:
                content=build.parse_sections(build.transform(upstream(),spec,variant,manifest))
                groups=build.validate_variant(content,spec,variant,manifest,upstream())
                build.validate_rules(content,groups)
                self.assertEqual({g for g in groups if g.endswith('节点')},
                                 set() if variant['mode'] == 'stable' else set(countries))

    def test_direct_rule_to_omitted_country_still_fails_without_redirect(self):
        fixture=upstream().replace('FINAL,PROXY','DOMAIN-SUFFIX,example.net,美国节点\nFINAL,PROXY')
        manifest=daily_v2()
        variant=spec.VARIANTS[4]
        content=build.parse_sections(build.transform(fixture,spec,variant,manifest))
        groups=build.validate_variant(content,spec,variant,manifest,fixture)
        with self.assertRaises(build.Failure):
            build.validate_rules(content,groups)

    def test_new_countries_follow_existing_countries_in_every_mode(self):
        lines = upstream().splitlines()
        original_countries = [line for line in lines if line.split(" = ", 1)[0].endswith("节点")]
        reversed_countries = iter(reversed(original_countries))
        fixture = "\n".join(next(reversed_countries) if line in original_countries else line for line in lines)
        fixture = fixture.replace("[Rule]", "尾部服务 = select,PROXY\n[Rule]")
        expected_existing = [build.split_params(line)[0] for line in reversed(original_countries)]
        for variant in spec.VARIANTS:
            with self.subTest(variant=variant["id"]):
                content = build.parse_sections(build.transform(fixture, spec, variant, selection()))
                groups = build.validate_variant(content, spec, variant, selection(), fixture)
                build.validate_rules(content, groups)
                names = [build.split_params(line)[0] for line in build.effective(content["proxy group"])]
                expected = ([] if variant['mode'] == 'stable' else expected_existing +
                            (["澳大利亚节点"] if variant["audience"] == "personal" else []))
                self.assertEqual([name for name in names if name.endswith("节点")], expected)
                if not expected:
                    self.assertEqual(names[-1], "尾部服务")
                    continue
                start = names.index(expected[0])
                self.assertEqual(names[start:start + len(expected)], expected)
                self.assertLess(names.index("游戏平台"), start)
                self.assertEqual(names[start + len(expected)], "尾部服务")

    def test_variant_validation_rejects_split_country_groups(self):
        for variant in spec.VARIANTS:
            if variant['mode'] == 'stable':
                continue
            with self.subTest(variant=variant["id"]):
                content = sections(variant)
                lines = content["proxy group"]
                country = next(line for line in lines if line.startswith("美国节点 = "))
                lines.remove(country)
                lines.insert(0, country)
                with self.assertRaisesRegex(build.Failure, "地区分组必须连续排列"):
                    build.validate_variant(content, spec, variant, selection(), upstream())

    def test_proxy_data_and_missing_or_cyclic_group_rejected(self):
        variant = spec.VARIANTS[1]
        manifest = selection()
        content = sections(variant)
        content["proxy"].append("node = trojan,example.invalid,443,password=secret")
        with self.assertRaises(build.Failure):
            build.validate_variant(content, spec, variant, manifest, upstream())
        content = sections(variant)
        content["proxy group"] = [line for line in content["proxy group"] if not line.startswith("速度 = ")]
        with self.assertRaises(build.Failure):
            build.validate_variant(content, spec, variant, manifest, upstream())
        content = sections(variant)
        content["proxy group"].append("Bad = select,Missing")
        with self.assertRaises(build.Failure):
            build.validate_groups(content)
        content = sections(variant)
        content["proxy group"].extend(["CycleA = select,CycleB", "CycleB = select,CycleA"])
        with self.assertRaises(build.Failure):
            build.validate_groups(content)

    def test_variant_validation_rejects_ai_escape_and_wrong_final(self):
        variant = spec.VARIANTS[2]
        manifest = selection()
        content = sections(variant)
        content["proxy group"] = [line.replace("AI = select,稳定", "AI = select,稳定,美国节点")
                                  for line in content["proxy group"]]
        with self.assertRaises(build.Failure):
            build.validate_variant(content, spec, variant, manifest, upstream())
        content = sections(variant)
        content["rule"] = [line.replace("FINAL,PROXY", "FINAL,速度") for line in content["rule"]]
        with self.assertRaises(build.Failure):
            build.validate_variant(content, spec, variant, manifest, upstream())

    def test_empty_stable_cannot_silently_gain_direct_or_proxy_members(self):
        for variant in spec.VARIANTS[:3]:
            if not variant["strict_stable"]:
                continue
            for extra in ("DIRECT", "PROXY", "policy-regex-filter=.*"):
                content = sections(variant)
                content["proxy group"] = [line + "," + extra if line.startswith("稳定 = ") else line
                                          for line in content["proxy group"]]
                with self.assertRaises(build.Failure):
                    build.validate_variant(content, spec, variant, None, upstream())

    def test_special_groups_cannot_be_reintroduced_or_removed_from_required_modes(self):
        for variant in spec.VARIANTS:
            required = {"select": set(), "hybrid": {"稳定"},
                        "fallback": {"速度", "稳定"}, "stable": {"稳定"}}[variant["mode"]]
            source = next(v for v in spec.VARIANTS
                          if v["audience"] == variant["audience"] and v["mode"] == "fallback")
            source_lines = dict((build.split_params(line)[0], line)
                                for line in build.effective(sections(source)["proxy group"]))
            for name in ("速度", "稳定"):
                with self.subTest(variant=variant["id"], group=name):
                    content = sections(variant)
                    if name in required:
                        content["proxy group"] = [line for line in content["proxy group"]
                                                  if not line.startswith(name + " = ")]
                    else:
                        content["proxy group"].append(source_lines[name])
                    with self.assertRaises(build.Failure):
                        build.validate_variant(content, spec, variant, selection(), upstream())

    def test_stable_mode_rejects_other_proxy_exits_and_keeps_direct_rules(self):
        variant = next(v for v in spec.VARIANTS if v['mode'] == 'stable')
        result = sections(variant)
        self.assertEqual({name for name, _ in (build.split_params(line) for line in
                          build.effective(result['proxy group'])) if name.endswith('节点')}, set())
        self.assertIn('FINAL,稳定', result['rule'])
        self.assertIn('DOMAIN-SUFFIX,cn.example,DIRECT', result['rule'])
        for changed in ('YouTube = select,PROXY', 'YouTube = select,稳定,PROXY',
                        'YouTube = select,美国节点'):
            broken = sections(variant)
            broken['proxy group'] = [changed if line.startswith('YouTube = ') else line
                                     for line in broken['proxy group']]
            with self.subTest(changed=changed), self.assertRaises(build.Failure):
                build.validate_variant(broken, spec, variant, selection(), upstream())
        broken = sections(variant)
        broken['proxy group'].append('美国节点 = url-test,PROXY')
        with self.assertRaises(build.Failure):
            build.validate_variant(broken, spec, variant, selection(), upstream())


if __name__ == "__main__":
    unittest.main()
