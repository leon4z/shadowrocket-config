import importlib.util
from pathlib import Path
import unittest

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
    def test_three_variants_route_as_requested_and_share_curated_groups(self):
        self.assertEqual([v["id"] for v in spec.VARIANTS], [
            "Shadowrocket-select", "Shadowrocket-fallback", "Shadowrocket-hybrid"])
        for variant in spec.VARIANTS:
            with self.subTest(variant=variant["id"]):
                content = sections(variant)
                groups = build.validate_variant(content, spec, variant, selection())
                build.validate_rules(content, groups)
                definitions = dict(build.split_params(line) for line in build.effective(content["proxy group"]))
                target = variant["default_policy"]
                self.assertIn(target, definitions["YouTube"])
                self.assertEqual(definitions["Spotify"][1], "DIRECT")
                self.assertIn("DOMAIN-SUFFIX,cn.example,DIRECT", content["rule"])
                self.assertIn(f"FINAL,{target}", content["rule"])
                self.assertEqual(definitions["速度"][0], "fallback")
                self.assertEqual(definitions["稳定"][0], "fallback")
                self.assertNotIn("tolerance=100", definitions["速度"])
                self.assertEqual(definitions["澳大利亚节点"][0], "url-test")
                self.assertIn("tolerance=100", definitions["澳大利亚节点"])
                self.assertIn("url=https://www.gstatic.com/generate_204", definitions["澳大利亚节点"])
                self.assertEqual(definitions["澳大利亚节点"][1],
                                 "policy-regex-filter=(?i)^(?:Sydney\\x2c01)$")
                self.assertFalse(build.effective(content["proxy"]))
                self.assertIn(f"update-url = {spec.RELEASE_URL_BASE}{variant['id']}.conf",
                              build.effective(content["general"]))
                if variant["strict_stable"]:
                    self.assertEqual(definitions["AI"], ["select", "稳定"])
                    self.assertEqual(definitions["谷歌服务"], ["select", "稳定"])
                else:
                    self.assertIn("PROXY", definitions["AI"])
                    self.assertIn("PROXY", definitions["谷歌服务"])

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

    def test_proxy_data_and_missing_or_cyclic_group_rejected(self):
        variant = spec.VARIANTS[1]
        manifest = selection()
        content = sections(variant)
        content["proxy"].append("node = trojan,example.invalid,443,password=secret")
        with self.assertRaises(build.Failure):
            build.validate_variant(content, spec, variant, manifest)
        content = sections(variant)
        content["proxy group"] = [line for line in content["proxy group"] if not line.startswith("速度 = ")]
        with self.assertRaises(build.Failure):
            build.validate_variant(content, spec, variant, manifest)
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
            build.validate_variant(content, spec, variant, manifest)
        content = sections(variant)
        content["rule"] = [line.replace("FINAL,PROXY", "FINAL,速度") for line in content["rule"]]
        with self.assertRaises(build.Failure):
            build.validate_variant(content, spec, variant, manifest)


if __name__ == "__main__":
    unittest.main()
