"""Interpret Cabal test evidence separately from lint, build, and Nix failures."""

import time
from pathlib import Path
from xml.etree import ElementTree

from .process import run_process


def test_evidence(results):
    failures, errors, executed = [], [], 0
    for name, result in results.items():
        if not name.startswith("test:"):
            continue
        xml_reports = result.get("junit_xml", [])
        if not xml_reports:
            raise ValueError(f"Missing JUnit evidence for {name}")
        command_failures = []
        command_executed = 0
        for xml in xml_reports:
            try:
                root = ElementTree.fromstring(xml)
            except ElementTree.ParseError as error:
                raise ValueError(f"Malformed JUnit evidence for {name}") from error
            for suite in root.iter():
                if suite.tag not in {"testsuite", "testsuites"}:
                    continue
                if int(suite.get("errors", "0")):
                    raise ValueError(f"JUnit suite reports runner errors for {name}")
                declared = int(suite.get("failures", "0"))
                if declared > sum(
                    case.find("failure") is not None for case in suite.iter("testcase")
                ):
                    raise ValueError(f"JUnit suite omits failing cases for {name}")
            for case in root.iter("testcase"):
                if case.find("skipped") is not None:
                    continue
                executed += 1
                command_executed += 1
                identifier = ".".join(
                    part for part in (case.get("classname", ""), case.get("name", "")) if part
                )
                if not identifier:
                    raise ValueError("JUnit testcase has no identity")
                if case.find("error") is not None:
                    errors.append(identifier)
                if case.find("failure") is not None:
                    command_failures.append(identifier)
        if not command_executed:
            raise ValueError(f"No tests executed for {name}")
        if result["exit_code"] != 0 and not command_failures:
            raise ValueError(f"{name} failed without identified assertion failures")
        failures.extend(command_failures)
    return {"failures": sorted(set(failures)), "errors": errors, "executed": executed}


def classify_validation(results, expected_failures, *, phase):
    if phase not in {"red", "green"}:
        raise ValueError("Validation phase must be red or green")
    if not {"lint", "nix"} <= results.keys() or not any(
        name.startswith("test:") for name in results
    ):
        return "blocked"
    if results["lint"]["exit_code"] != 0:
        return "blocked"
    try:
        evidence = test_evidence(results)
    except ValueError:
        return "blocked"
    if evidence["errors"] or evidence["executed"] == 0:
        return "blocked"
    if phase == "green":
        return (
            "green"
            if not evidence["failures"] and all(r["exit_code"] == 0 for r in results.values())
            else "blocked"
        )
    if not expected_failures or set(evidence["failures"]) != set(expected_failures):
        return "blocked"
    return "expected_red" if results["nix"]["exit_code"] == 0 else "needs_red_review"


class LocalExecutor:
    def __init__(self, *, nix_flake=None, timeout=3600):
        self.nix_flake = str(Path(nix_flake).expanduser().resolve()) if nix_flake else None
        self.timeout = timeout

    def __call__(self, argv, cwd):
        actual = ["nix", "develop", self.nix_flake or str(cwd), "--command", *argv]
        started = time.time_ns()
        code, stdout, stderr = run_process(actual, cwd, timeout=self.timeout)
        reports = []
        if argv[:2] == ["just", "test"]:
            for path in (cwd / "test-reports").glob("*.xml"):
                if not path.is_symlink() and path.stat().st_mtime_ns >= started:
                    reports.append(path.read_text())
        return {
            "argv": actual,
            "exit_code": code,
            "stdout": stdout,
            "stderr": stderr,
            "junit_xml": reports,
        }
