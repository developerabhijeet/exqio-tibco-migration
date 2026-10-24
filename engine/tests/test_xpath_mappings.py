"""The XPath functions a TIBCO mapping uses, translated by code instead of handed to a model.

A mapping used to go to the Code Builder whole as soon as one binding used ``concat`` or
``substring``. The model then had to re-derive every path in it from the comments, and a path like
``$QueryAccount/Record[1]/ssn`` reads as a ``Record`` property but is an element of a row list, so
what came back did not compile. These functions each have one meaning, so code writes them; what is
left for a model is only what genuinely needs judgement.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from engine.dotnet_gen import generator, xpath
from engine.tibco_parser.scanner import scan_folder

GMF = Path(__file__).resolve().parents[2] / "samples" / "synthetic-gmf"


# ------------------------------------------------------------------ the shape both sides read
@pytest.mark.parametrize("expr, name, args", [
    ("concat($a/b, ' ', $c/d)", "concat", ["$a/b", "' '", "$c/d"]),
    ("substring($a/b, 8, 4)", "substring", ["$a/b", "8", "4"]),
    ("concat('x', substring($a/b, 1, 2))", "concat", ["'x'", "substring($a/b, 1, 2)"]),
    ("concat($a, ',')", "concat", ["$a", "','"]),  # a comma inside a literal is not a separator
    ("current-dateTime()", "current-dateTime", []),
])
def test_a_call_splits_into_its_name_and_top_level_arguments(expr, name, args):
    assert xpath.call(expr) == (name, args)


@pytest.mark.parametrize("expr", ["$a/b", "'literal'", "42", "($a) + ($b)", "concat($a, $b", "", "a + b"])
def test_what_is_not_one_call_is_not_read_as_one(expr):
    assert xpath.call(expr) is None


def test_the_design_and_the_generator_agree_on_what_code_can_write():
    assert xpath.is_translatable("$ReceiveFundingRequest/Body/tns:FundingRequest/tns:dealerId")
    assert xpath.is_translatable("concat(normalize-space($a/b), '%')")
    assert xpath.is_translatable("concat('***-**-', substring($a/b, 8, 4))")
    # a function code has no single meaning for, and one with the wrong number of arguments
    assert not xpath.is_translatable("format-number($a/b, '#.##')")
    assert not xpath.is_translatable("substring($a/b)")
    assert not xpath.is_translatable("$a/b + $c/d")


# ------------------------------------------------------------------ the translation itself
@pytest.fixture(scope="module")
def pg() -> generator.ProcGen:
    """AccountLookup: a REST starter and one JDBC query, which is all these expressions need."""
    from engine.domains.models import IOCard
    from engine.dotnet_gen.design import build_design_skeleton

    app = {x.id: x for x in scan_folder(GMF).applications}["gmf.credit"]
    pid = "loanservicing.AccountLookup"
    proc = next(p for p in app.processes if p.id == pid)
    rec = build_design_skeleton(proc, app, [], [], IOCard(process_id=pid))
    sol = generator.SolutionGen(app, [pid], {pid: rec}, {})
    return generator.ProcGen(sol, proc, rec, [], [], [])


@pytest.mark.parametrize("expr, cs", [
    ("$QueryAccount/Record[1]/status", "state.QueryAccount.FirstOrDefault()?.Status"),
    ("normalize-space($QueryAccount/Record[1]/status)",
     "TibText.NormalizeSpace((state.QueryAccount.FirstOrDefault()?.Status ?? string.Empty))"),
    ("upper-case($QueryAccount/Record[1]/status)",
     "(state.QueryAccount.FirstOrDefault()?.Status ?? string.Empty).ToUpperInvariant()"),
    ("substring-after($QueryAccount/Record[1]/status, '-')",
     'TibText.SubstringAfter((state.QueryAccount.FirstOrDefault()?.Status ?? string.Empty), "-")'),
    ("translate($QueryAccount/Record[1]/status, '-', '')",
     'TibText.Translate((state.QueryAccount.FirstOrDefault()?.Status ?? string.Empty), "-", "")'),
])
def test_a_string_function_becomes_the_helper_with_the_same_meaning(pg, expr, cs):
    assert pg.assign(expr, "string", True, "field") == cs


def test_a_row_list_step_is_an_element_not_a_property(pg):
    """The exact mistake a model made from the comment alone: ``Record[1]`` is the first row."""
    out = pg.assign("concat('***-**-', substring($QueryAccount/Record[1]/ssn, 8, 4))", "string", True, "maskedSsn")
    assert out == ('string.Concat("***-**-", TibText.Substring('
                   '(state.QueryAccount.FirstOrDefault()?.Ssn ?? string.Empty), 8d, 4d))')
    assert ".Record" not in out


def test_a_value_that_is_not_a_string_is_converted_the_way_value_of_converts_it(pg):
    """A number inside a concat takes the XPath string form, not the C# one."""
    out = pg.assign("concat($QueryAccount/Record[1]/days_past_due, ' days')", "string", True, "note")
    assert out == ('string.Concat(TibValue.Of(state.QueryAccount.FirstOrDefault()?.DaysPastDue).AsString(), '
                   '" days")')


def test_a_translated_string_still_converts_to_the_target_type(pg):
    """The target decides the conversion; the function only produces the string to convert."""
    out = pg.assign("normalize-space($QueryAccount/Record[1]/principal_balance)", "decimal", False, "amount")
    assert out.startswith("TibValue.Of(TibText.NormalizeSpace(")
    assert out.endswith('.AsDecimal() ?? throw TibValue.Missing("amount")')


def test_current_date_time_keeps_the_offset_xs_dateTime_carries(pg):
    assert pg.assign("current-dateTime()", "DateTimeOffset", True, "at") == "DateTimeOffset.Now"
    assert pg.assign("current-dateTime()", "DateTime", True, "at") == "DateTime.Now"


def test_number_of_a_path_converts_that_path_to_the_numeric_target(pg):
    out = pg.assign("number($QueryAccount/Record[1]/days_past_due)", "int", True, "days")
    assert out == "TibValue.Of(TibValue.Of(state.QueryAccount.FirstOrDefault()?.DaysPastDue).AsString()).AsInt()"


def test_a_function_code_cannot_translate_still_goes_to_the_model(pg):
    assert pg.assign("format-number($QueryAccount/Record[1]/principal_balance, '#.##')",
                     "string", True, "amount") is None
    assert pg.assign("$QueryAccount/Record[1]/days_past_due + 1", "int", True, "days") is None


# ------------------------------------------- what the model is given when a mapping is still its job
def test_a_mapping_left_to_the_model_carries_every_path_already_resolved():
    """The comments alone are not enough to write the code: they are TIBCO paths, not C#."""
    from engine.domains.models import IOCard
    from engine.dotnet_gen.design import build_design_skeleton

    app = {x.id: x for x in scan_folder(GMF).applications}["gmf.credit"]
    pid = "loanservicing.PaymentPosting"
    proc = next(p for p in app.processes if p.id == pid)
    rec = build_design_skeleton(proc, app, [], [], IOCard(process_id=pid))
    sol = generator.SolutionGen(app, [pid], {pid: rec}, {})
    pg = generator.ProcGen(sol, proc, rec, [], [], [])

    text, origin = generator.gen_mappings(pg)
    assert origin == "agent"  # this process still has work only a model can do
    body = text.split("AssessLateFeeMapping")[1].split("    }")[0]
    assert "// TIBCO: LateFeeAssessment/daysLate = days-from-duration(" in body  # no single meaning in C#
    assert "//   $LoadAccount/Record[1]/next_due_date -> state.LoadAccount.FirstOrDefault()?.NextDueDate" in body
    assert 'throw new NotImplementedException("agent fills: AssessLateFee");' in body


def test_the_legend_names_a_path_once_and_leaves_out_what_does_not_resolve(pg):
    seen = pg.legend(["concat($QueryAccount/Record[1]/ssn, $QueryAccount/Record[1]/ssn, $nowhere/at/all)"])
    assert seen == [("$QueryAccount/Record[1]/ssn", "state.QueryAccount.FirstOrDefault()?.Ssn")]


def test_a_namespace_sorts_before_its_children_so_dotnet_format_accepts_the_file():
    """``System.Text.Json`` must precede ``System.Text.Json.Nodes``.

    Sorting the rendered lines put it after, because ``.`` sorts before ``;``. The solution still
    compiled, so only ``dotnet format`` saw it: the build failed its format step on code that was
    otherwise correct.
    """
    text = generator._masked(
        "using System.Text.Json.Nodes;\nusing System.Text.Json.Serialization;\nusing System.Text.Json;\n"
        "\npublic sealed record Row\n{\n    public string? Ssn { get; init; }\n}\n", "GmfCredit")
    assert text.splitlines()[:4] == [
        "using System.Text.Json;",
        "using System.Text.Json.Nodes;",
        "using System.Text.Json.Serialization;",
        "using GmfCredit.Core.Contracts;",
    ]
