"""Executable release-candidate smoke checks for the public package."""
from __future__ import annotations

import copy
import json
import os
import stat
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest import mock

from scripts import release_smoke


def _fixture_members(version="1.1.0"):
    dist_info = f"pocketchat-{version}.dist-info"
    members = {name: ("payload:" + name).encode("utf-8")
               for name in release_smoke.required_members()}
    members.update({
        f"{dist_info}/METADATA": (
            "Metadata-Version: 2.1\n"
            "Name: pocketchat\n"
            f"Version: {version}\n\n"
        ).encode(),
        f"{dist_info}/WHEEL": b"Wheel-Version: 1.0\nTag: py3-none-any\n",
        f"{dist_info}/RECORD": b"",
        f"{dist_info}/entry_points.txt": (
            b"[console_scripts]\n"
            b"pocketchat = pocketchat.cli:main\n"
        ),
    })
    return members


def _write_fixture_wheel(root, *, version="1.1.0", members=None):
    path = Path(root) / f"pocketchat-{version}-py3-none-any.whl"
    payloads = dict(_fixture_members(version) if members is None else members)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in payloads.items():
            archive.writestr(name, data)
    return path, payloads


def _write_installed_tree(root, members):
    site_packages = Path(root) / "site-packages"
    for name, data in members.items():
        target = site_packages / Path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return site_packages


class PackageManifestTests(unittest.TestCase):
    def test_wheel_manifest_is_stable_and_body_free(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wheel, payloads = _write_fixture_wheel(temp_dir)
            before = wheel.read_bytes()
            first = release_smoke.build_package_manifest(
                wheel, source_kind="wheel")
            second = release_smoke.build_package_manifest(
                wheel, source_kind="wheel")
            after = wheel.read_bytes()

        self.assertEqual(first, second)
        self.assertEqual(before, after)
        self.assertEqual("pocketchat-package-manifest.v1", first["schema"])
        self.assertEqual(1, first["schema_version"])
        self.assertEqual("wheel", first["source_kind"])
        self.assertEqual("1.1.0", first["version"])
        self.assertEqual(
            {"name": "pocketchat", "target": "pocketchat.cli:main"},
            first["entry_point"],
        )
        self.assertEqual(sorted(payloads),
                         [item["path"] for item in first["members"]])
        encoded = json.dumps(first, sort_keys=True)
        self.assertNotIn(str(wheel.parent), encoded)
        self.assertNotIn("payload:pocketchat/__init__.py", encoded)

    def test_installed_tree_matches_wheel_and_ignores_generated_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wheel, payloads = _write_fixture_wheel(temp_dir)
            site_packages = _write_installed_tree(temp_dir, payloads)
            generated = {
                "pocketchat/__pycache__/cli.cpython-39.pyc": b"pyc",
                "pocketchat-1.1.0.dist-info/INSTALLER": b"pip\n",
                "pocketchat-1.1.0.dist-info/REQUESTED": b"",
                "pocketchat-1.1.0.dist-info/direct_url.json": b"{}",
            }
            _write_installed_tree(temp_dir, generated)
            before = {path: (site_packages / path).read_bytes()
                      for path in payloads}
            wheel_manifest = release_smoke.build_package_manifest(
                wheel, source_kind="wheel")
            installed_manifest = release_smoke.build_package_manifest(
                site_packages, source_kind="installed_tree")
            after = {path: (site_packages / path).read_bytes()
                     for path in payloads}

        self.assertEqual(before, after)
        self.assertEqual(
            [item["path"] for item in wheel_manifest["members"]],
            [item["path"] for item in installed_manifest["members"]],
        )
        self.assertEqual(
            [item["sha256"] for item in wheel_manifest["members"]],
            [item["sha256"] for item in installed_manifest["members"]],
        )
        encoded = json.dumps(installed_manifest, sort_keys=True)
        self.assertNotIn("__pycache__", encoded)
        self.assertNotIn("INSTALLER", encoded)
        self.assertNotIn("direct_url.json", encoded)
        self.assertNotIn(str(site_packages), encoded)

    def test_generated_metadata_scope_cannot_hide_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            members = _fixture_members()
            members["pocketchat/evil.dist-info/INSTALLER"] = b"pip\n"
            members["pocketchat/evil.dist-info/RECORD"] = b"wheel record\n"
            wheel, _ = _write_fixture_wheel(temp_dir, members=members)
            site_packages = _write_installed_tree(temp_dir, members)
            wheel_manifest = release_smoke.build_package_manifest(
                wheel, source_kind="wheel")
            installed_manifest = release_smoke.build_package_manifest(
                site_packages, source_kind="installed_tree")

        nested_installer = "pocketchat/evil.dist-info/INSTALLER"
        nested_record = "pocketchat/evil.dist-info/RECORD"
        self.assertIn(
            nested_installer,
            [item["path"] for item in wheel_manifest["members"]],
        )
        self.assertIn(
            nested_installer,
            [item["path"] for item in installed_manifest["members"]],
        )
        self.assertIn(
            nested_record,
            [item["path"] for item in wheel_manifest["members"]],
        )

        drifted = copy.deepcopy(installed_manifest)
        next(item for item in drifted["members"]
             if item["path"] == nested_record)["sha256"] = "f" * 64
        with self.assertRaisesRegex(release_smoke.ReleaseSmokeError,
                                    "payload drift"):
            release_smoke._validate_manifest_pair(
                wheel_manifest, drifted)

        with tempfile.TemporaryDirectory() as temp_dir:
            members = _fixture_members()
            members["pocketchat-evil.dist-info/INSTALLER"] = b"pip\n"
            wheel, _ = _write_fixture_wheel(temp_dir, members=members)
            with self.assertRaisesRegex(
                    release_smoke.ReleaseSmokeError,
                    "one pocketchat dist-info"):
                release_smoke.build_package_manifest(
                    wheel, source_kind="wheel")

    def test_required_resources_and_identity_fail_closed(self):
        cases = []
        missing_module = _fixture_members()
        missing_module.pop("pocketchat/__init__.py")
        cases.append(("module", missing_module))
        missing_schema = _fixture_members()
        missing_schema.pop(release_smoke.REQUIRED_SCHEMAS[0])
        cases.append(("schema", missing_schema))
        missing_metadata = _fixture_members()
        missing_metadata.pop("pocketchat-1.1.0.dist-info/RECORD")
        cases.append(("metadata", missing_metadata))
        wrong_version = _fixture_members()
        wrong_version["pocketchat-1.1.0.dist-info/METADATA"] = (
            b"Metadata-Version: 2.1\nName: pocketchat\nVersion: 2.0.0\n\n")
        cases.append(("version", wrong_version))
        wrong_entry = _fixture_members()
        wrong_entry["pocketchat-1.1.0.dist-info/entry_points.txt"] = (
            b"[console_scripts]\npocketchat = pocketchat.other:main\n")
        cases.append(("entry point", wrong_entry))
        second_dist = _fixture_members()
        second_dist["pocketchat-2.0.0.dist-info/METADATA"] = b"x"
        cases.append(("dist-info", second_dist))

        for message, members in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temp_dir:
                wheel, _ = _write_fixture_wheel(temp_dir, members=members)
                with self.assertRaisesRegex(release_smoke.ReleaseSmokeError,
                                            message):
                    release_smoke.build_package_manifest(
                        wheel, source_kind="wheel")

    def test_unsafe_duplicate_collision_encryption_and_link_are_refused(self):
        path_cases = [
            ("unsafe", "../escape.py", "unsafe"),
            ("case", "pocketchat/CLI.py", "collid"),
            ("unicode", "pocketchat/e\u0301.py", "collid"),
            ("noncanonical", "pocketchat//other.py", "unsafe"),
        ]
        for label, name, message in path_cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                members = _fixture_members()
                if label == "case":
                    members["pocketchat/cli.py"] = b"a"
                if label == "unicode":
                    members["pocketchat/\u00e9.py"] = b"a"
                members[name] = b"b"
                wheel, _ = _write_fixture_wheel(temp_dir, members=members)
                with self.assertRaisesRegex(release_smoke.ReleaseSmokeError,
                                            message):
                    release_smoke.build_package_manifest(
                        wheel, source_kind="wheel")

        with tempfile.TemporaryDirectory() as temp_dir:
            wheel = Path(temp_dir) / "duplicate.whl"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(wheel, "w") as archive:
                    archive.writestr("pocketchat/__init__.py", b"a")
                    archive.writestr("pocketchat/__init__.py", b"b")
            with self.assertRaisesRegex(release_smoke.ReleaseSmokeError,
                                        "duplicate"):
                release_smoke.build_package_manifest(wheel, source_kind="wheel")

        class Info:
            filename = "pocketchat/__init__.py"
            file_size = 1
            external_attr = 0
            flag_bits = 1
            def is_dir(self):
                return False

        class Archive:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def infolist(self):
                return [Info()]
            def read(self, info):
                return b"x"

        with mock.patch.object(
                release_smoke.zipfile, "ZipFile", return_value=Archive()), \
                self.assertRaisesRegex(release_smoke.ReleaseSmokeError,
                                       "encrypted"):
            release_smoke.build_package_manifest(
                Path("encrypted.whl"), source_kind="wheel")
        Info.flag_bits = 0
        Info.external_attr = (stat.S_IFLNK | 0o777) << 16
        with mock.patch.object(
                release_smoke.zipfile, "ZipFile", return_value=Archive()), \
                self.assertRaisesRegex(release_smoke.ReleaseSmokeError,
                                       "non-regular"):
            release_smoke.build_package_manifest(
                Path("link.whl"), source_kind="wheel")

    def test_installed_symlink_is_refused(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            payloads = _fixture_members()
            site_packages = _write_installed_tree(temp_dir, payloads)
            target = site_packages / "target.py"
            target.write_bytes(b"target")
            link = site_packages / "pocketchat" / "__init__.py"
            link.unlink()
            try:
                os.symlink(target, link)
            except OSError as exc:
                self.skipTest(f"symlink fixture unavailable: {exc}")
            with self.assertRaisesRegex(release_smoke.ReleaseSmokeError,
                                        "symlink|non-regular"):
                release_smoke.build_package_manifest(
                    site_packages, source_kind="installed_tree")

    def test_limits_and_unknown_source_kind_fail_before_truncation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wheel, payloads = _write_fixture_wheel(temp_dir)
            limits = [
                ({"max_members": len(payloads) - 1}, "count"),
                ({"max_member_bytes": max(map(len, payloads.values())) - 1},
                 "member"),
                ({"max_total_bytes": sum(map(len, payloads.values())) - 1},
                 "total"),
                ({"max_report_bytes": 1}, "report"),
            ]
            for kwargs, message in limits:
                with self.subTest(kwargs=kwargs), self.assertRaisesRegex(
                        release_smoke.ReleaseSmokeError, message):
                    release_smoke.build_package_manifest(
                        wheel, source_kind="wheel", **kwargs)
            for key in ("max_members", "max_member_bytes",
                        "max_total_bytes", "max_report_bytes"):
                with self.subTest(invalid=key), self.assertRaisesRegex(
                        release_smoke.ReleaseSmokeError, "positive"):
                    release_smoke.build_package_manifest(
                        wheel, source_kind="wheel", **{key: True})

        with mock.patch.object(
                release_smoke, "_wheel_entries") as reader, \
                self.assertRaisesRegex(release_smoke.ReleaseSmokeError,
                                       "source kind"):
            release_smoke.build_package_manifest(
                Path("ignored"), source_kind="directory")
        reader.assert_not_called()

    def test_manifest_validation_rejects_category_and_group_drift(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            payloads = _fixture_members()
            site_packages = _write_installed_tree(temp_dir, payloads)
            manifest = release_smoke.build_package_manifest(
                site_packages, source_kind="installed_tree")
        mutations = []
        unknown_category = copy.deepcopy(manifest)
        unknown_category["members"][0]["category"] = "secret"
        mutations.append(unknown_category)
        group_drift = copy.deepcopy(manifest)
        group_drift["groups"]["modules"] = []
        mutations.append(group_drift)
        schema_drift = copy.deepcopy(manifest)
        schema_drift["schema_version"] = 2
        mutations.append(schema_drift)
        dist_info_drift = copy.deepcopy(manifest)
        for member in dist_info_drift["members"]:
            member["path"] = member["path"].replace(
                "pocketchat-1.1.0.dist-info", "pocketchat-9.9.9.dist-info")
        dist_info_drift["groups"]["metadata"] = [
            path.replace("pocketchat-1.1.0.dist-info", "pocketchat-9.9.9.dist-info")
            for path in dist_info_drift["groups"]["metadata"]
        ]
        mutations.append(dist_info_drift)
        for candidate in mutations:
            with self.subTest(
                    candidate=candidate.get("schema_version")), \
                    self.assertRaisesRegex(release_smoke.ReleaseSmokeError,
                                           "manifest"):
                release_smoke.validate_installed({
                    "version": "pocketchat 1.1.0",
                    "commands": list(release_smoke.REQUIRED_TASK_COMMANDS),
                    "package_manifest": candidate,
                })

    def test_release_smoke_reports_both_manifests_and_refuses_drift(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wheel, payloads = _write_fixture_wheel(temp_dir)
            site_packages = _write_installed_tree(temp_dir, payloads)
            wheel_manifest = release_smoke.build_package_manifest(
                wheel, source_kind="wheel")
            installed_manifest = release_smoke.build_package_manifest(
                site_packages, source_kind="installed_tree")

        artifact = type("Artifact", (), {
            "path": Path("pocketchat-1.1.0-py3-none-any.whl"),
            "size": 1234,
            "sha256": "a" * 64,
            "members": tuple(item["path"] for item in wheel_manifest["members"]),
            "version": "1.1.0",
            "manifest": wheel_manifest,
        })()
        installed = {
            "version": "1.1.0",
            "commands": list(release_smoke.REQUIRED_TASK_COMMANDS),
            "package_manifest": installed_manifest,
        }
        result = release_smoke.run_release_smoke(
            Path("repo"), Path("out"),
            build=lambda *args, **kwargs: artifact,
            install=lambda *args, **kwargs: installed,
        )
        self.assertEqual(wheel_manifest, result["package_manifests"]["wheel"])
        self.assertEqual(installed_manifest,
                         result["package_manifests"]["installed"])

        drifted = copy.deepcopy(installed)
        drifted["package_manifest"]["members"][0]["sha256"] = "f" * 64
        with self.assertRaisesRegex(release_smoke.ReleaseSmokeError,
                                    "manifest.*drift"):
            release_smoke.run_release_smoke(
                Path("repo"), Path("out"),
                build=lambda *args, **kwargs: artifact,
                install=lambda *args, **kwargs: drifted,
            )



class WorkflowLintTests(unittest.TestCase):
    def test_ci_runs_repository_lint_before_tests(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github" /
                    "workflows" / "ci.yml").read_text(encoding="utf-8")
        lint = workflow.find("- name: Run repository lint")
        tests = workflow.find("- name: Run the suite")
        self.assertGreaterEqual(lint, 0)
        self.assertGreater(tests, lint)
        self.assertIn('python -m pip install "ruff==0.17.3"', workflow)
        self.assertIn("python -m ruff check --select E9,F pocketchat tests", workflow)


class WorkflowTests(unittest.TestCase):
    def test_workflow_runs_and_uploads_the_release_smoke(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github" /
                    "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("release-smoke:", workflow)
        self.assertIn("scripts/release_smoke.py", workflow)
        self.assertIn("actions/upload-artifact@", workflow)
        self.assertIn("pocketchat-release-smoke", workflow)
        self.assertNotIn("rm -rf", workflow)


class ReleaseSmokeTests(unittest.TestCase):
    def test_success_reports_wheel_digest_and_installed_task_surface(self):
        calls = []
        with tempfile.TemporaryDirectory() as temp_dir:
            wheel, payloads = _write_fixture_wheel(temp_dir)
            site_packages = _write_installed_tree(temp_dir, payloads)
            wheel_manifest = release_smoke.build_package_manifest(
                wheel, source_kind="wheel")
            installed_manifest = release_smoke.build_package_manifest(
                site_packages, source_kind="installed_tree")
            artifact = type("Artifact", (), {
                "path": Path("/tmp/pocketchat-1.1.0-py3-none-any.whl"),
                "size": 1234,
                "sha256": "a" * 64,
                "members": tuple(item["path"]
                                 for item in wheel_manifest["members"]),
                "version": "1.1.0",
                "manifest": wheel_manifest,
            })()

            def build(*args, **kwargs):
                calls.append(("build", args, kwargs))
                self.assertTrue(kwargs["isolated"])
                return artifact

            def install(*args, **kwargs):
                calls.append(("install", args, kwargs))
                return {
                    "version": "1.1.0",
                    "commands": list(release_smoke.REQUIRED_TASK_COMMANDS),
                    "package_manifest": installed_manifest,
                }

            result = release_smoke.run_release_smoke(
                Path("/repo"), Path("/tmp/out"), build=build, install=install,
                python="python3")
        self.assertTrue(result["ok"])
        self.assertEqual("1.1.0", result["version"])
        self.assertEqual("a" * 64, result["wheel_sha256"])
        self.assertEqual(1234, result["wheel_bytes"])
        self.assertEqual(sorted(release_smoke.REQUIRED_TASK_COMMANDS),
                         result["commands"])
        self.assertEqual(wheel_manifest, result["package_manifests"]["wheel"])
        self.assertEqual(installed_manifest,
                         result["package_manifests"]["installed"])
        self.assertEqual(2, len(calls))

    def test_missing_schema_or_removed_surface_is_refused(self):
        bad_members = (
            "pocketchat/__init__.py",
            "pocketchat/task_exec.py",
            "pocketchat/web/server.py",
        )
        artifact = release_smoke.WheelArtifact(
            Path("/tmp/bad.whl"), 1, "b" * 64, bad_members, "1.1.0", {})
        with self.assertRaisesRegex(release_smoke.ReleaseSmokeError,
                                    "schema|web"):
            release_smoke.validate_artifact(artifact)

    def test_build_command_uses_isolation_by_default(self):
        manifest = {
            "schema": release_smoke.PACKAGE_MANIFEST_SCHEMA,
            "schema_version": 1,
            "source_kind": "wheel",
            "package": "pocketchat",
            "version": "1.1.0",
            "entry_point": {
                "name": "pocketchat",
                "target": release_smoke.PACKAGE_ENTRY_POINT,
            },
            "groups": {
                "metadata": [],
                "modules": list(release_smoke.REQUIRED_MODULES),
                "package_data": [],
                "schemas": list(release_smoke.REQUIRED_SCHEMAS),
            },
            "members": [
                {
                    "path": name,
                    "category": "schema" if name.endswith(".json") else "module",
                    "bytes": 1,
                    "sha256": "a" * 64,
                }
                for name in sorted(release_smoke.required_members())
            ],
        }
        with mock.patch.object(release_smoke, "_run") as run, \
                mock.patch.object(release_smoke, "build_package_manifest",
                                  return_value=manifest), \
                mock.patch.object(release_smoke, "validate_artifact"):
            root = Path(tempfile.mkdtemp())
            (root / "pyproject.toml").write_text("[build-system]\n", encoding="utf-8")
            out = root / "dist"
            out.mkdir(parents=True)
            wheel = out / "pocketchat-1.1.0-py3-none-any.whl"
            wheel.write_bytes(b"wheel")
            release_smoke.build_wheel(root, out)
        command = run.call_args.args[0]
        self.assertNotIn("--no-build-isolation", command)

    def test_installed_cli_version_prefix_matches_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            _wheel, payloads = _write_fixture_wheel(temp_dir)
            site_packages = _write_installed_tree(temp_dir, payloads)
            manifest = release_smoke.build_package_manifest(
                site_packages, source_kind="installed_tree")
        release_smoke.validate_installed({
            "version": "pocketchat 1.1.0",
            "commands": list(release_smoke.REQUIRED_TASK_COMMANDS),
            "package_manifest": manifest,
        })

    def test_installed_cli_must_expose_every_required_task_command(self):
        with self.assertRaisesRegex(release_smoke.ReleaseSmokeError,
                                    "installed CLI"):
            release_smoke.validate_installed(
                {"version": "1.1.0",
                 "commands": ["task exec", "task verify"]})

    def test_wheel_member_reader_rejects_duplicate_or_unsafe_members(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            duplicate = Path(temp_dir) / "duplicate.whl"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(duplicate, "w") as archive:
                    archive.writestr("a.py", b"a")
                    archive.writestr("a.py", b"b")
            with self.assertRaises(release_smoke.ReleaseSmokeError):
                release_smoke.read_wheel_members(duplicate)

            unsafe = Path(temp_dir) / "unsafe.whl"
            with zipfile.ZipFile(unsafe, "w") as archive:
                archive.writestr("../escape.py", b"a")
            with self.assertRaises(release_smoke.ReleaseSmokeError):
                release_smoke.read_wheel_members(unsafe)

    def test_json_output_is_stable_and_contains_no_absolute_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wheel, payloads = _write_fixture_wheel(temp_dir)
            site_packages = _write_installed_tree(temp_dir, payloads)
            wheel_manifest = release_smoke.build_package_manifest(
                wheel, source_kind="wheel")
            installed_manifest = release_smoke.build_package_manifest(
                site_packages, source_kind="installed_tree")
        artifact = release_smoke.WheelArtifact(
            Path("/tmp/pocketchat.whl"), 1, "c" * 64,
            tuple(item["path"] for item in wheel_manifest["members"]),
            "1.1.0", wheel_manifest)
        result = release_smoke.ReleaseSmokeResult(artifact, {
            "version": "1.1.0",
            "commands": list(release_smoke.REQUIRED_TASK_COMMANDS),
            "package_manifest": installed_manifest,
        })
        payload = result.as_dict()
        encoded = json.dumps(payload, sort_keys=True)
        self.assertNotIn("/tmp/", encoded)
        self.assertEqual("pocketchat.whl", payload["wheel"])



if __name__ == "__main__":
    unittest.main()
