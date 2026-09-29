import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
module_spec = importlib.util.spec_from_file_location(
    "render_mihomo_under_test", ROOT / "scripts" / "render_mihomo.py")
renderer = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(renderer)
REVISION = "a" * 40
NATIVE_PAYLOAD = b"# native Clash provider\npayload:\n  - DOMAIN-SUFFIX,example.com\n"


def native_payload(_url):
    return NATIVE_PAYLOAD


class MihomoRendererTest(unittest.TestCase):
    def test_bundle_uses_native_providers_and_keeps_safe_order(self):
        bundle = renderer.render_bundle(fetcher=native_payload, revision=REVISION)
        providers = bundle["rule_providers"]
        names = [provider["name"] for provider in providers]
        rules = bundle["rules"]

        self.assertEqual(bundle["schema_version"], 1)
        self.assertEqual(bundle["kind"], "mihomo-rule-bundle")
        self.assertEqual(bundle["upstream_commit"], REVISION)
        self.assertEqual(names[0], "lan")
        self.assertLess(names.index("china"), names.index("global"))
        self.assertEqual(rules[-1], "MATCH,PROXY")
        self.assertLess(rules.index("RULE-SET,china,DIRECT"),
                        rules.index("RULE-SET,global,PROXY"))
        self.assertLess(rules.index("GEOIP,CN,DIRECT"),
                        rules.index("RULE-SET,global,PROXY"))
        self.assertEqual(rules[:2], [
            "DOMAIN-SUFFIX,diabrowser.engineering,AI",
            "DOMAIN-SUFFIX,claude.dev,AI",
        ])
        self.assertIn("Spotify", bundle["policy_contract"]["required"])
        self.assertNotIn("Spotify", bundle["policy_contract"]["protected"])
        self.assertEqual(bundle["policy_contract"]["missing_protected_policy"], "REJECT")

        for provider in providers:
            with self.subTest(provider=provider["name"]):
                self.assertEqual(provider["type"], "http")
                self.assertEqual(provider["behavior"], "classical")
                self.assertEqual(provider["format"], "yaml")
                self.assertIn(f"/{REVISION}/rule/Clash/", provider["url"])
                self.assertNotIn("/master/", provider["url"])
                self.assertTrue(provider["url"].endswith(".yaml"))
                self.assertTrue(provider["path"].startswith("ruleset/"))
                self.assertNotIn("..", Path(provider["path"]).parts)
                self.assertEqual(provider["sha256"], hashlib.sha256(NATIVE_PAYLOAD).hexdigest())

    def test_large_split_categories_use_complete_standalone_files(self):
        providers = {entry["name"]: entry for entry in renderer.provider_entries(REVISION)}
        self.assertTrue(providers["netflix"]["url"].endswith("Netflix_Classical.yaml"))
        self.assertTrue(providers["apple"]["url"].endswith("Apple_Classical.yaml"))
        self.assertTrue(providers["global"]["url"].endswith("Global_Classical.yaml"))
        self.assertTrue(providers["china"]["url"].endswith(
            "ChinaMax_Classical_No_IPv6.yaml"))

    def test_shared_custom_rules_are_read_directly_and_scoped_for_gateway(self):
        document = [
            {"rule": "DOMAIN-SUFFIX,EXAMPLE.COM,AI", "note": "all"},
            {"rule": "DOMAIN,stable.example.com,稳定", "note": "stable",
             "modes": ["fallback"], "audiences": ["personal"]},
            {"rule": "DOMAIN,fast.example.com,速度", "note": "speed",
             "modes": ["fallback"], "audiences": ["personal"]},
            {"rule": "DOMAIN,other.example.com,DIRECT", "note": "other",
             "audiences": ["generic"]},
            {"rule": "DOMAIN,manual.example.com,PROXY", "note": "manual",
             "modes": ["select"]},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "custom-rules.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            self.assertEqual(renderer.load_custom_rules(path), [
                "DOMAIN-SUFFIX,example.com,AI",
                "DOMAIN,stable.example.com,AI",
                "DOMAIN,fast.example.com,PROXY",
            ])

    def test_invalid_or_empty_native_payload_fails_build(self):
        for body in (b"", b"rules:\n  - DOMAIN,x.example\n", b"payload:\n",
                     b"payload:\n  - DOMAIN-SUFFIX,example.com\nbroken: [\n",
                     b"payload:\n  - DOMAIN-SUFFIX,example.com\n  - [broken\n"):
            with self.subTest(body=body), self.assertRaises(renderer.Failure):
                renderer.validate_native_payload(body, "broken", "DIRECT")
        with patch.object(renderer, "load_custom_rules", return_value=[]), \
             self.assertRaises(renderer.Failure):
            renderer.render_bundle(fetcher=lambda _url: b"payload:\n", revision=REVISION)

    def test_revision_resolution_and_direct_catch_all_are_fail_closed(self):
        self.assertEqual(renderer.resolve_upstream_commit(
            lambda _url: json.dumps({"sha": REVISION}).encode()), REVISION)
        for response in (b"{}", b'{"sha":"master"}', b"not-json"):
            with self.subTest(response=response), self.assertRaises(renderer.Failure):
                renderer.resolve_upstream_commit(lambda _url, data=response: data)
        for terminal in (b"payload:\n  - MATCH\n", b"payload:\n  - FINAL\n"):
            with self.subTest(terminal=terminal), self.assertRaisesRegex(
                    renderer.Failure, "catch-all"):
                renderer.validate_native_payload(terminal, "direct-provider", "DIRECT")

    def test_duplicate_custom_conditions_and_unsafe_provider_paths_fail(self):
        duplicate = [
            {"rule": "DOMAIN,x.example.com,AI", "note": "one"},
            {"rule": "DOMAIN,X.EXAMPLE.COM,DIRECT", "note": "two"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "custom-rules.json"
            path.write_text(json.dumps(duplicate), encoding="utf-8")
            with self.assertRaisesRegex(renderer.Failure, "duplicate"):
                renderer.load_custom_rules(path)

        providers = renderer.provider_entries(REVISION)
        providers[0] = {**providers[0], "path": "../private.yaml"}
        with self.assertRaisesRegex(renderer.Failure, "unsafe"):
            renderer.validate_provider_contract(providers, REVISION)

    def test_output_is_atomic_deterministic_and_has_no_private_proxy_fields(self):
        bundle = renderer.render_bundle(fetcher=native_payload, revision=REVISION)
        encoded = json.dumps(bundle, ensure_ascii=False, sort_keys=True)
        for secret_field in ('"server"', '"port"', '"password"', '"uuid"',
                             '"private-key"', '"proxy-providers"'):
            self.assertNotIn(secret_field, encoded)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "mihomo" / "bundle.json"
            renderer.write_bundle(bundle, output)
            first = output.read_bytes()
            renderer.write_bundle(bundle, output)
            self.assertEqual(output.read_bytes(), first)
            self.assertFalse(any(output.parent.glob("bundle.json.*")))


if __name__ == "__main__":
    unittest.main()
