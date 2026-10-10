"""Build metrics with fixed formulas (design spec 2.5): the numbers on the tab 5 radar.

Every metric is a fraction 0..1 (``None`` when its denominator is zero, shown as "n/a"); the
``formulas`` dict spells each formula out in words for the UI, and ``details`` gives the counts
behind each number so the Evidence drawer can show them.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from engine.analysis.models import Rule
from engine.dotnet_gen.csharp_contracts import pascal
from engine.dotnet_gen.runner import BuildResult, TestRun
from engine.dotnet_gen.trx import BRANCH_SUFFIX, RULES_SUFFIX, SCENARIO_SUFFIX, TestCase

APPROVED = ("approved", "edited")

FORMULAS: dict[str, str] = {
    "rule_carry_forward": "approved rules with a passing test / approved rules (edited rules count as "
                          "approved; a rule's test is the RulesTests case whose display name starts "
                          "with the rule id)",
    "test_pass_rate": "passed tests / total tests in the TRX results (skipped tests count as not passed)",
    "branch_coverage": "IR branches with a passing test / IR branches (a branch is a conditional or "
                       "otherwise transition; its test is the BranchTests case named with the "
                       "transition id)",
    "code_coverage": "coverlet line coverage of the generated service projects (Api, Core, "
                     "Infrastructure): lines covered / lines valid from the Cobertura report",
    "standards_adherence": "1 minus (compiler and analyzer warnings / C# files), floored at 0; 0 when "
                           "dotnet format --verify-no-changes does not pass",
    "compile": "dotnet build of the solution passes (exit code 0), with the warning count",
    "infra_validity": "terraform validate and terragrunt hclfmt --check pass (n/a until the "
                      "infrastructure is generated)",
}


class Metrics(BaseModel):
    rule_carry_forward: float | None
    test_pass_rate: float | None
    branch_coverage: float | None
    code_coverage: float | None
    standards_adherence: float
    compile_ok: bool
    warnings: int
    infra_ok: bool | None
    formulas: dict[str, str] = Field(default_factory=lambda: dict(FORMULAS))
    details: dict[str, str] = Field(default_factory=dict)


def _ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def compute_metrics(rules: list[Rule], branches: int, test_run: TestRun, build: BuildResult,
                    format_ok: bool, infra_ok: bool | None, process: str | None = None) -> Metrics:
    """Metrics per spec 2.5 from the approved rules, the IR branch count and the runner results.

    ``build.files`` (C# files counted by ``DotnetRunner.build``) is the standards denominator.
    ``process`` (a process id such as ``dealerfunding.DealerFundingRequest``, or its PascalCase name)
    scopes rule and branch matching to that process's test classes, so rule ``R1`` of one process
    never counts for ``R1`` of another; rules and branches are per process.
    """
    scope = pascal(process.rsplit(".", 1)[-1]) if process else None

    def mine(case: TestCase) -> bool:
        if scope is None:
            return True
        parts = case.name.rsplit(".", 2)  # <namespace>.<Process>RulesTests.<method>
        cls = parts[-2] if len(parts) >= 2 else ""
        return any(cls == f"{scope}{suffix}" for suffix in (RULES_SUFFIX, BRANCH_SUFFIX, SCENARIO_SUFFIX))

    approved = [r.id for r in rules if r.status in APPROVED]
    passing_rules = {c.rule_id for c in test_run.cases if c.rule_id and c.outcome == "passed" and mine(c)}
    carried = sum(1 for rid in approved if rid in passing_rules)

    total = len(test_run.cases)
    passed = sum(1 for c in test_run.cases if c.outcome == "passed")

    passing_branches = {c.branch_id for c in test_run.cases if c.branch_id and c.outcome == "passed" and mine(c)}
    covered_branches = min(len(passing_branches), branches)

    files = max(build.files, 0)
    if not format_ok:
        standards = 0.0
    elif files:
        standards = max(0.0, 1.0 - build.warnings / files)
    else:
        standards = 0.0 if build.warnings else 1.0

    details = {
        "rule_carry_forward": f"{carried} of {len(approved)} approved rules have a passing test",
        "test_pass_rate": f"{passed} of {total} tests passed",
        "branch_coverage": f"{covered_branches} of {branches} IR branches have a passing test",
        "code_coverage": ("no coverage report" if test_run.coverage_line is None
                          else f"{test_run.coverage_line:.1%} of lines covered"),
        "standards_adherence": (f"{build.warnings} warnings over {files} C# files; dotnet format "
                                f"{'passed' if format_ok else 'failed'}"),
        "compile": f"{'pass' if build.ok else 'fail'}, {build.warnings} warnings, {len(build.errors)} errors",
        "infra_validity": "not run" if infra_ok is None else ("pass" if infra_ok else "fail"),
    }
    return Metrics(
        rule_carry_forward=_ratio(carried, len(approved)),
        test_pass_rate=_ratio(passed, total),
        branch_coverage=_ratio(covered_branches, branches),
        code_coverage=test_run.coverage_line,
        standards_adherence=standards,
        compile_ok=build.ok,
        warnings=build.warnings,
        infra_ok=infra_ok,
        details=details,
    )
