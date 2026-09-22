import importlib.util
import contextlib
import io
from pathlib import Path
import re
import tempfile
import unittest
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


def sections(variant):
    manifest = build.validate_selection(selection(), spec)
    return build.parse_sections(build.transform(upstream(), spec, variant, manifest))


class BuildTest(unittest.TestCase):
    def test_six_variants_route_as_requested_and_use_separate_pools(self):
        self.assertEqual([v["id"] for v in spec.VARIANTS], [
            "Shadowrocket-select", "Shadowrocket-fallback", "Shadowrocket-hybrid",
            "leon4z-select", "leon4z-fallback", "leon4z-hybrid"])
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
                                    "fallback": {"速度", "稳定"}}[variant["mode"]]
                self.assertEqual(set(definitions) & {"速度", "稳定"}, expected_special)
                for name in expected_special:
                    self.assertEqual(definitions[name][0], "fallback")
                    self.assertFalse(any(p.startswith("tolerance=") for p in definitions[name]))
                for name, params in definitions.items():
                    if name.endswith("节点"):
                        self.assertEqual(params[0], "url-test")
                        self.assertIn("tolerance=100", params)
                if variant["audience"] == "personal":
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
                if variant["audience"] == "personal":
                    self.assertIn(f"update-url = {spec.RELEASE_URL_BASE}{variant['id']}.conf",
                                  build.effective(content["general"]))
                else:
                    self.assertFalse(any(line.split("=", 1)[0].strip().lower() == "update-url"
                                         for line in build.effective(content["general"])))
                if variant["strict_stable"]:
                    self.assertEqual(definitions["AI"], ["select", "稳定"])
                    self.assertEqual(definitions["谷歌服务"], ["select", "稳定"])
                else:
                    self.assertIn("PROXY", definitions["AI"])
                    self.assertIn("PROXY", definitions["谷歌服务"])

    def test_generic_removes_update_url_from_upstream_and_shared_overrides(self):
        for key in ("update-url", "UPDATE-URL"):
            fixture = upstream().replace("[General]", f"[General]\n{key} = https://example.com/old.conf")
            shared = {**spec.GENERAL_OVERRIDES, key: "https://example.com/shared.conf"}
            with patch.object(spec, "GENERAL_OVERRIDES", shared):
                for variant in spec.VARIANTS[:3]:
                    with self.subTest(key=key, variant=variant["id"]):
                        result = build.transform(fixture, spec, variant, None)
                        self.assertEqual(result, build.transform(upstream(), spec, variant, None))
                        self.assertFalse(any("update-url" in line.lower() for line in result))
                        build.validate_variant(build.parse_sections(result), spec, variant, None, fixture)
        fixture = upstream().replace("[General]", "[General]\nupdate-url = https://example.com/old.conf")
        for variant in spec.VARIANTS[3:]:
            result = build.parse_sections(build.transform(fixture, spec, variant, selection()))
            self.assertIn(f"update-url = {spec.RELEASE_URL_BASE}{variant['id']}.conf", result["general"])
            build.validate_variant(result, spec, variant, selection(), fixture)

    def test_update_url_validation_rejects_wrong_audience_or_target(self):
        for variant in spec.VARIANTS:
            content = sections(variant)
            content["general"] = [line for line in content["general"] if not line.startswith("update-url")]
            if variant["audience"] == "generic":
                content["general"].append("UPDATE-URL = https://example.com/unwanted.conf")
            with self.subTest(variant=variant["id"], change="missing_or_unwanted"):
                with self.assertRaises(build.Failure):
                    build.validate_variant(content, spec, variant, selection(), upstream())
            if variant["audience"] == "personal":
                content["general"].append("update-url = https://example.com/wrong.conf")
                with self.subTest(variant=variant["id"], change="wrong_target"):
                    with self.assertRaises(build.Failure):
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
        for variant in spec.VARIANTS[3:]:
            with self.assertRaises(build.Failure):
                build.transform(upstream(), spec, variant, None)

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

    def test_new_upstream_region_works_generically_but_needs_personal_samples(self):
        changed = upstream().replace("[Rule]", "加拿大节点 = url-test,policy-regex-filter=Canada\n[Rule]")
        generic, personal = spec.VARIANTS[0], spec.VARIANTS[3]
        output = build.transform(changed, spec, generic, None)
        self.assertIn("加拿大节点", build.validate_variant(build.parse_sections(output), spec, generic, None, changed))
        with self.assertRaises(build.Failure):
            build.transform(changed, spec, personal, selection())

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
                        "fallback": {"速度", "稳定"}}[variant["mode"]]
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


if __name__ == "__main__":
    unittest.main()
