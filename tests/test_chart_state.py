"""Pure chart ownership: geometry, retained attempts, passes and exact continuation."""

import builtins
import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from uuid import UUID

import pytest

from color_measurement import chart_state as chart


def uid(value):
    return str(UUID(int=value))


def context(*, phase="accepted", certainty="readback", **changes):
    values = dict(
        observer="CIE1931_2deg",
        units="relative",
        calibration="confirmed_ready",
        options={"averages": 1},
        mode="reflective_spot",
        serial="synthetic-01",
    )
    values.update(changes)
    return dict(
        phase=phase,
        material={
            key: dict(value=value, certainty=certainty, source="synthetic test facts")
            for key, value in values.items()
        },
        provenance={"scope": "software-only"},
        required_material=list(values),
    )


def state(*, rows=1, columns=2, facts=None):
    return chart.new_state(
        chart.definition("custom", name="Synthetic grid", rows=rows, columns=columns),
        facts or context(),
        run_id=uid(1),
        segment_id=uid(2),
        pass_id=uid(3),
    )


def begin(value, number=1, *, facts=None):
    return chart.begin_attempt(
        value,
        attempt_id=uid(100 + number),
        command_id=uid(200 + number),
        operation_id=uid(300 + number),
        request={"workflow": "spot", "patch_ids": ["native-target-9"], "options": {"averages": 1}},
        intended_context=facts or context(phase="intended"),
    )


def refs(value, number=1):
    return [
        dict(
            segment_id=value["segment_id"],
            operation_id=uid(300 + number),
            observation_id=f"native-observation-{number}",
            sample_id="native-target-9",
        )
    ]


def complete(value, number=1, *, facts=None, status="completed", check=None, readings=None):
    return chart.complete_attempt(
        value,
        uid(100 + number),
        status=status,
        reading_refs=refs(value, number) if readings is None else readings,
        accepted_context=facts or context(),
        position_check=check,
        reason="Synthetic terminal outcome",
    )


def accepted_source(value):
    payload = json.dumps(
        {"schema_version": 5, "batch_id": value["segment_id"], "chart": value},
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return payload, hashlib.sha256(payload).hexdigest()


def continue_chart(value, *, facts=None, **options):
    payload, digest = accepted_source(value)
    return chart.continue_state(
        value,
        facts or context(),
        segment_id=uid(900),
        source_id="accepted-synthetic/batch.json",
        source_bytes=payload,
        source_sha256=digest,
        **options,
    )


def reference():
    return dict(qualified=True, digest="a" * 64, meaning="Synthetic shared-science qualification")


def warning():
    return dict(
        available=True,
        warning=True,
        reference_digest="a" * 64,
        reason="Synthetic qualified placement warning",
        distance=12.0,
    )


def pending_position(*, prior=False):
    value = chart.set_reference(state(), reference())
    if prior:
        value = complete(begin(value))
        value = chart.select_cell(value, 1)
        number = 2
    else:
        number = 1
    return complete(begin(value, number), number, check=warning())


def test_preset_native_mapping_is_separate_from_canonical_coordinates():
    classic, sg = chart.definition("classic"), chart.definition("sg")
    assert (classic["rows"], classic["columns"]) == (4, 6)
    assert len(classic["cells"]) == 24
    assert classic["cells"][1] == dict(
        index=2, row=1, column=2, coordinate="B1", source_id=2, source_location="A2", source_row=2
    )
    assert (sg["rows"], sg["columns"]) == (10, 14) and len(sg["cells"]) == 140
    assert sg["cells"][1]["coordinate"] == "B1"
    assert sg["cells"][1]["source_id"] == 11 and sg["cells"][1]["source_location"] == "B01"
    assert chart.coordinate(10, 27) == "AA10"


def test_descriptive_patch_names_are_derived_without_changing_stored_chart_identity():
    for definition in (
        chart.definition("classic"), chart.definition("sg"),
        chart.definition("custom", name="Classic-shaped custom grid", rows=4, columns=6),
    ):
        value = chart.new_state(
            definition, context(), run_id=uid(1), segment_id=uid(2), pass_id=uid(3)
        )
        original = deepcopy(value)
        view = chart.view(value)
        assert value == original
        assert all("patch_name" not in cell for cell in value["definition"]["cells"])
        if definition["kind"] == "classic":
            assert view["cells"][1]["patch_name"] == "Light skin"
            assert view["cells"][1]["coordinate"] == "B1"
            assert view["cells"][1]["source_location"] == "A2"
            assert view["cells"][18]["patch_name"] == "White"
            assert view["cells"][20]["patch_name"] == "Neutral 6.5"
            assert view["cells"][23]["patch_name"] == "Black"
            rotated = chart.view(chart.rotate(value, 90))
            assert [cell["patch_name"] for cell in rotated["cells"]] == [
                cell["patch_name"] for cell in view["cells"]
            ]
        else:
            assert all(cell["patch_name"] is None for cell in view["cells"])


@pytest.mark.parametrize(
    "rows,columns", [(0, 2), (True, 2), (2.0, 3), (141, 1), (11, 14), (10**100, 10**100)]
)
def test_custom_dimensions_refuse_before_allocating_cells(rows, columns):
    with pytest.raises(ValueError):
        chart.definition("custom", name="Grid", rows=rows, columns=columns)


def test_ti2_authentication_requires_axis_confirmation_and_keeps_source_identity():
    path = Path(__file__).parent / "fixtures/targets/asymmetric-six-patch.target.json"
    packet = path.read_text()
    with pytest.raises(ValueError, match="Confirm"):
        chart.definition("ti2", target_json=packet, strips_as="rows")
    rows = chart.definition("ti2", target_json=packet, strips_as="rows", mapping_confirmed=True)
    columns = chart.definition(
        "ti2", target_json=packet, strips_as="columns", mapping_confirmed=True
    )
    assert (rows["rows"], rows["columns"]) == (2, 3)
    assert (columns["rows"], columns["columns"]) == (3, 2)
    assert rows["cells"][4]["source_id"] == columns["cells"][3]["source_id"] == "blk-41"
    assert rows["cells"][4]["source_row"] == columns["cells"][3]["source_row"] == 1
    assert rows["cells"][4]["source_location"] == "02B"
    imported = chart.new_state(rows, context(), run_id=uid(1), segment_id=uid(2), pass_id=uid(3))
    summary = chart.view(imported)
    assert "target_json" not in summary["definition"] and len(json.dumps(summary)) < 8192
    assert summary["cells"][4]["source_id"] == "blk-41"
    assert all(cell["patch_name"] is None for cell in summary["cells"])
    tampered = json.loads(packet)
    tampered["rows"][0]["sample_id"] = "replacement"
    with pytest.raises(ValueError, match="projection changed"):
        chart.definition(
            "ti2", target_json=json.dumps(tampered), strips_as="rows", mapping_confirmed=True
        )


def test_rotation_and_inspection_never_retarget_a_frozen_active_attempt():
    original = state(rows=2, columns=3)
    started = begin(chart.select_cell(original, 5))
    frozen = deepcopy(started["passes"][0]["attempts"][0])
    rotated = chart.rotate(started, 90)
    inspected = chart.inspect_cell(rotated, 2)
    assert original["current_cell"] == 1 and not original["passes"][0]["attempts"]
    assert inspected["current_cell"] == 5 and inspected["inspection_cell"] == 2
    assert inspected["passes"][0]["attempts"][0] == frozen
    assert inspected["revision"] == rotated["revision"]
    with pytest.raises(ValueError, match="still active"):
        chart.select_cell(inspected, 2)
    done = complete(inspected)
    attempt = done["passes"][0]["attempts"][0]
    assert attempt["cell"] == 5 and attempt["readings"][0]["sample_id"] == "native-target-9"
    assert done["current_cell"] == 6 and done["orientation"] == 90


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_failed_retry_keeps_successful_selected_reading_and_partial_history(status):
    prior = complete(begin(state()))
    retry = begin(chart.select_cell(prior, 1), 2)
    outcome = complete(retry, 2, status=status)
    view = chart.view(outcome)
    assert view["coverage"] == 1 and view["cells"][0]["selected_attempt"] == uid(101)
    assert view["cells"][0]["attempt_count"] == 2
    assert (
        chart.attempt_details(outcome, view["cells"][0]["latest_attempt"]["id"])["readings"][0]
        ["observation_id"] == "native-observation-2"
    )
    assert outcome["current_cell"] == 1


@pytest.mark.parametrize("readings", [None, {}, "", 0, True])
def test_complete_attempt_refuses_nonlist_reading_references_without_changing_state(readings):
    original = begin(state())
    before = deepcopy(original)
    with pytest.raises(ValueError, match="Chart reading references must be a list"):
        chart.complete_attempt(
            original, uid(101), status="completed", reading_refs=readings,
            accepted_context=context(),
        )
    assert original == before


def test_complete_attempt_normalizes_tuple_reading_references_to_plain_state():
    original = begin(state())
    readings = refs(original)
    result = chart.complete_attempt(
        original, uid(101), status="completed", reading_refs=tuple(readings),
        accepted_context=context(),
    )
    assert result["passes"][0]["attempts"][0]["readings"] == readings
    assert chart.view(result)["coverage"] == 1


def test_failed_attempt_retains_absent_accepted_context_without_fabricating_it():
    original = begin(state())
    failed = chart.complete_attempt(
        original,
        uid(101),
        status="failed",
        reading_refs=[],
        accepted_context=None,
        reason="No retained reading",
    )
    assert failed["passes"][0]["attempts"][0]["accepted_context"] is None
    assert chart.view(failed)["coverage"] == 0 and failed["pending_context"] is None


def test_terminal_completion_is_idempotent_and_cannot_replace_retained_identity():
    done = complete(begin(state()))
    assert complete(done) == done
    changed = refs(done)
    changed[0]["observation_id"] = "another-reading"
    with pytest.raises(ValueError, match="different outcome"):
        complete(done, readings=changed)
    tampered = deepcopy(done)
    tampered["passes"][0]["attempts"][0]["request"]["patch_ids"] = ["another-target"]
    with pytest.raises(ValueError, match="request changed"):
        chart.validate_state(tampered)


@pytest.mark.parametrize(
    "key,value",
    [
        ("observer", "CIE1964_10deg"),
        ("units", "cd/m2"),
        ("calibration", "another accepted calibration"),
        ("options", {"averages": 2}),
    ],
)
def test_material_change_needs_explicit_new_pass_and_does_not_pool_coverage(key, value):
    prior = complete(begin(state()))
    changed = context(phase="intended", **{key: value})
    proposed = chart.propose_context(prior, changed)
    assert proposed["pending_context"]["differences"][0]["key"] == key
    assert chart.view(proposed)["coverage"] == 1
    with pytest.raises(ValueError, match="resolve its material context"):
        begin(proposed, 2, facts=changed)
    paused = chart.set_paused(proposed, True)
    with pytest.raises(ValueError, match="before resuming"):
        chart.set_paused(paused, False)
    accepted = chart.accept_context(paused, pass_id=uid(4))
    assert chart.view(accepted)["coverage"] == 0
    assert chart.view(accepted, pass_id=uid(3))["coverage"] == 1
    assert accepted["current_cell"] == 1 and accepted["paused"]


def test_requested_reversion_clears_pending_but_returned_change_remains_explicit():
    prior = complete(begin(state()))
    proposed = chart.propose_context(prior, context(phase="intended", units="cd/m2"))
    reverted = chart.propose_context(proposed, context(phase="intended"))
    assert reverted["pending_context"] is None
    returned = complete(begin(prior, 2), 2, facts=context(units="cd/m2"))
    assert returned["passes"][0]["attempts"][-1]["status"] == "unresolved"
    assert returned["pending_context"]["requires_decision"]
    assert chart.propose_context(returned, context(phase="intended"))["pending_context"] is not None
    assert complete(returned, 2, facts=context(units="cd/m2")) == returned


def test_certainty_enrichment_and_provenance_only_changes_do_not_split_passes():
    before = context(phase="intended", certainty="requested")
    after = context()
    assert chart.context_difference(before, after) == []
    before["material"]["observer"] = dict(value=None, certainty="unknown", source="not reported")
    assert chart.context_difference(before, after) == []
    after["provenance"]["new_detail"] = "retained readback"
    assert chart.context_difference(before, after) == []
    lost = deepcopy(after)
    lost["material"]["observer"]["certainty"] = "unknown"
    assert chart.context_difference(after, lost)[0]["kind"] == "lost"


def test_later_is_unchanged_and_pending_guard_is_scoped_to_chart_mutation():
    pending = pending_position()
    token = pending["passes"][0]["pending_position"]["token"]
    assert chart.resolve_position(pending, token, "later") == pending
    assert chart.inspect_cell(pending, 2)["current_cell"] == 1
    assert chart.rotate(pending, 270)["passes"] == pending["passes"]
    for action in (
        lambda: begin(pending, 2),
        lambda: chart.select_cell(pending, 2),
        lambda: chart.set_paused(pending, True),
        lambda: chart.finish(pending, allow_incomplete=True),
    ):
        with pytest.raises(ValueError, match="Keep or Retry"):
            action()
    with pytest.raises(ValueError, match="Stale"):
        chart.resolve_position(pending, "changed-token", "keep")


@pytest.mark.parametrize("action", ["keep", "retry"])
def test_position_decision_preserves_original_cell_and_prior_success(action):
    pending = pending_position(prior=True)
    token = pending["passes"][0]["pending_position"]["token"]
    resolved = chart.resolve_position(pending, token, action)
    assert resolved["passes"][0]["pending_position"] is None
    expected = uid(102) if action == "keep" else uid(101)
    assert resolved["passes"][0]["selected_attempts"][0] == expected
    assert resolved["current_cell"] == (2 if action == "keep" else 1)
    assert len(resolved["passes"][0]["attempts"]) == 2
    assert resolved["passes"][0]["position_decisions"][-1]["attempt_id"] == uid(102)


def test_unqualified_position_warning_cannot_be_promoted_to_a_decision():
    started = begin(state())
    with pytest.raises(ValueError, match="qualified shared-science"):
        complete(started, check=warning())
    unavailable = dict(available=False, warning=False, reason="Reference conditions unavailable")
    done = complete(started, check=unavailable)
    assert chart.view(done)["coverage"] == 1
    assert done["passes"][0]["pending_position"] is None


def test_history_selection_cannot_retarget_or_pool_another_pass():
    prior = complete(begin(state()))
    with pytest.raises(ValueError, match="original cell"):
        chart.select_attempt(prior, pass_id=uid(3), cell=2, attempt_id=uid(101))
    changed = chart.accept_context(
        chart.propose_context(prior, context(units="cd/m2")), pass_id=uid(4)
    )
    with pytest.raises(ValueError, match="inspection only"):
        chart.select_attempt(changed, pass_id=uid(3), cell=1, attempt_id=uid(101))
    assert chart.view(changed, pass_id=uid(3))["cells"][0]["selected_attempt"] == uid(101)


def test_incomplete_finish_requires_explicit_choice_and_never_allows_active_attempt():
    with pytest.raises(ValueError, match="incomplete"):
        chart.finish(state())
    done = chart.finish(state(), allow_incomplete=True)
    assert done["finished"] and chart.view(done)["incomplete"]
    with pytest.raises(ValueError, match="still active"):
        chart.finish(begin(state()), allow_incomplete=True)


def test_continuation_binds_exact_bytes_and_keeps_original_native_reading_identities(monkeypatch):
    source = complete(begin(state()))
    original = deepcopy(source)
    payload, digest = accepted_source(source)
    monkeypatch.setattr(
        builtins, "open", lambda *args, **kwargs: pytest.fail("Pure helper opened file")
    )
    linked = continue_chart(source)
    assert source == original and linked["segment_id"] != source["segment_id"]
    assert linked["passes"] == source["passes"] and chart.view(linked)["coverage"] == 1
    assert linked["passes"][0]["attempts"][0]["readings"] == refs(source)
    envelope = linked["sources"][-1]
    assert envelope == dict(
        source_id="accepted-synthetic/batch.json", segment_id=source["segment_id"], sha256=digest
    )
    with pytest.raises(ValueError, match="bytes changed"):
        chart.continue_state(
            source,
            context(),
            segment_id=uid(901),
            source_id="synthetic",
            source_bytes=payload + b"\n",
            source_sha256=digest,
        )
    edited = json.loads(payload)
    edited["chart"]["current_cell"] = 1
    altered = json.dumps(edited).encode()
    with pytest.raises(ValueError, match="do not bind"):
        chart.continue_state(
            source,
            context(),
            segment_id=uid(901),
            source_id="synthetic",
            source_bytes=altered,
            source_sha256=hashlib.sha256(altered).hexdigest(),
        )


def test_continuation_unknown_selected_facts_cannot_be_certified_by_later_enrichment():
    unknown = context()
    unknown["material"]["serial"] = dict(value=None, certainty="unknown", source="not reported")
    source = complete(
        begin(state(facts=unknown), facts=dict(unknown, phase="intended")), facts=unknown
    )
    enriched = complete(begin(source, 2, facts=dict(unknown, phase="intended")), 2, facts=context())
    assert enriched["passes"][0]["context"]["material"]["serial"]["certainty"] == "readback"
    with pytest.raises(ValueError, match="established on both sides"):
        continue_chart(enriched)


@pytest.mark.parametrize("unestablished_in", ["source pass", "current context", "selected attempt"])
def test_continuation_refusal_names_first_unestablished_fact_and_its_source(unestablished_in):
    unknown = context()
    for key in ("serial", "observer"):
        unknown["material"][key] = dict(value=None, certainty="unknown", source="not reported")
    source_facts = context() if unestablished_in == "current context" else unknown
    source = complete(begin(state(facts=source_facts),
                            facts=dict(source_facts, phase="intended")), facts=source_facts)
    if unestablished_in == "selected attempt":
        source = complete(begin(source, 2, facts=dict(unknown, phase="intended")),
                          2, facts=context())
    target = dict(unknown if unestablished_in == "current context" else context(),
                  phase="intended")
    before = deepcopy((source, target))
    with pytest.raises(ValueError, match="observer.*" + unestablished_in):
        continue_chart(source, facts=target)
    assert (source, target) == before


def test_new_pass_continuation_retains_pending_history_without_pooling_it():
    source = pending_position(prior=True)
    linked = continue_chart(
        source, facts=context(units="cd/m2"), action="new-pass", new_pass_id=uid(4)
    )
    assert linked["active_pass"] == uid(4) and chart.view(linked)["coverage"] == 0
    old = chart.view(linked, pass_id=uid(3))
    assert old["coverage"] == 1 and old["pending_position"] is not None
    assert linked["passes"][0]["attempts"] == source["passes"][0]["attempts"]


def test_continued_pending_position_rekeys_only_decision_owner_not_native_source():
    source = pending_position()
    linked = continue_chart(source)
    old, new = source["passes"][0]["pending_position"], linked["passes"][0]["pending_position"]
    assert old["token"] != new["token"] and new["segment_id"] == linked["segment_id"]
    assert linked["passes"][0]["attempts"] == source["passes"][0]["attempts"]
    kept = chart.resolve_position(linked, new["token"], "keep")
    assert chart.view(kept)["coverage"] == 1
    assert kept["passes"][0]["attempts"][0]["readings"][0]["segment_id"] == source["segment_id"]


def test_inherited_empty_pass_keeps_frozen_reference_context():
    source = chart.set_reference(state(), reference())
    linked = continue_chart(source)
    with pytest.raises(ValueError, match="reference is frozen"):
        chart.set_reference(linked, dict(reference(), meaning="changed"))


def test_accepted_context_cannot_be_replaced_by_requested_intent():
    started = begin(state())
    with pytest.raises(ValueError, match="substitute intended facts"):
        complete(started, facts=context(phase="intended", certainty="requested"))
    assert started["passes"][0]["attempts"][0]["status"] == "started"


def test_selected_placement_warning_requires_the_frozen_keep_receipt():
    pending = pending_position()
    changed = deepcopy(pending)
    changed["passes"][0]["selected_attempts"][0] = uid(101)
    with pytest.raises(ValueError, match="Keep decision"):
        chart.validate_state(changed)
    token = pending["passes"][0]["pending_position"]["token"]
    kept = chart.resolve_position(pending, token, "keep")
    kept["passes"][0]["position_decisions"][0]["token"] = "b" * 64
    with pytest.raises(ValueError, match="frozen reading"):
        chart.validate_state(kept)


def test_started_attempt_and_inherited_sources_cannot_claim_fabricated_receipts():
    started = begin(state())
    changed = deepcopy(started)
    changed["passes"][0]["attempts"][0]["accepted_context"] = context()
    with pytest.raises(ValueError, match="Started chart attempt"):
        chart.validate_state(changed)
    done = complete(started)
    done["passes"][0]["attempts"][0]["segment_id"] = uid(800)
    done["passes"][0]["attempts"][0]["readings"][0]["segment_id"] = uid(800)
    with pytest.raises(ValueError, match="Terminal chart outcome changed"):
        chart.validate_state(done)


def test_duplicate_or_parallel_attempt_admission_preserves_the_original_state():
    started = begin(state())
    original = deepcopy(started)
    with pytest.raises(ValueError, match="still active"):
        begin(started, 2)
    assert started == original


def test_documented_pure_chart_example_runs_with_current_api():
    document = Path(__file__).resolve().parents[1] / "docs/chart-state.md"
    example = re.search(r"```python\n(.*?)\n```", document.read_text(), re.DOTALL)
    assert example is not None
    namespace = {}
    exec(compile(example[1], str(document), "exec"), namespace)
    assert namespace["summary"]["coverage"] == 1


def test_public_context_validator_preserves_native_facts_and_rejects_undeclared_required_keys():
    from color_measurement.chart_state import validate_context

    facts = context()
    facts["material"]["observer"] = dict(value=None, certainty="unknown", source="not reported")
    facts["provenance"]["native_text"] = "Original native\nmetadata"
    validated = validate_context(facts)
    assert validated == facts and validated is not facts
    assert validated["material"]["observer"]["value"] is None
    facts["required_material"].append("invented_quantity")
    with pytest.raises(ValueError, match="Required chart material"):
        validate_context(facts)


def test_continuation_metadata_growth_is_linear_without_new_attempts():
    value = state(rows=1, columns=1)
    sizes = []
    for generation in range(9):
        payload, digest = accepted_source(value)
        sizes.append(len(payload))
        if generation == 8:
            break
        original_segment = value["segment_id"]
        value = chart.continue_state(
            value,
            context(),
            segment_id=uid(1000 + generation),
            source_id=f"accepted-source-{generation}",
            source_bytes=payload,
            source_sha256=digest,
        )
        assert value["sources"][-1] == dict(
            source_id=f"accepted-source-{generation}",
            segment_id=original_segment,
            sha256=digest,
        )
    assert max(after - before for before, after in zip(sizes, sizes[1:])) < 1024
    assert len(value["sources"]) == 8 and not value["passes"][0]["attempts"]


def test_compact_view_and_one_attempt_details_exclude_repeated_history_and_raw_packets():
    value = begin(state())
    packet_marker = "retained-packet-marker-" * 8192
    attempt = value["passes"][0]["attempts"][0]
    attempt["request"]["retained_metadata"] = packet_marker
    attempt["request_sha256"] = hashlib.sha256(
        json.dumps(attempt["request"], sort_keys=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()
    value = complete(value)
    for number in range(2, 7):
        value = complete(begin(chart.select_cell(value, 1), number), number, status="failed")
    first_pass = value["active_pass"]
    for number in range(4, 20):
        current_context = value["passes"][-1]["context"]
        if not value["passes"][-1]["attempts"]:
            started = begin(value, 100 + number, facts=dict(current_context, phase="intended"))
            value = complete(started, 100 + number, facts=current_context, status="failed")
        value = chart.accept_context(
            chart.propose_context(value, context(options={"averages": number})), pass_id=uid(number)
        )
    summary = chart.view(value, pass_id=first_pass)
    encoded = json.dumps(summary)
    assert packet_marker not in encoded and len(encoded) < 8192
    assert "passes" not in summary and "sources" not in summary
    assert "target_json" not in summary["definition"]
    assert summary["pass_count"] == 17 and summary["attempt_count"] == 6
    assert summary["total_attempt_count"] == 21 and summary["source_count"] == 0
    cell = summary["cells"][0]
    assert "attempts" not in cell and cell["attempt_count"] == 6
    assert cell["selected_attempt"] == uid(101)
    assert cell["latest_attempt"] == dict(id=uid(106), status="failed", result_status="failed")
    details = chart.attempt_details(value, uid(101), pass_id=first_pass)
    assert details["pass_id"] == first_pass
    assert details["request"]["retained_metadata"] == packet_marker
    details["request"]["retained_metadata"] = "local edit"
    assert chart.attempt_details(value, uid(101))["request"]["retained_metadata"] == packet_marker
    with pytest.raises(ValueError, match="Unknown chart attempt"):
        chart.attempt_details(value, uid(101), pass_id=value["active_pass"])


def test_history_pages_chronological_cell_attempts_without_pooling_passes_or_mutating():
    value = complete(begin(state()))
    for cell, number in ((1, 2), (2, 3), (1, 4)):
        value = complete(begin(chart.select_cell(value, cell), number), number, status="failed")
    value = chart.start_pass(value, pass_id=uid(4))
    value = complete(begin(value, 5), 5, status="failed")
    original = deepcopy(value)
    page = chart.history(value, uid(3), 1)
    assert page == dict(total=3, offset=0, attempts=[
        dict(id=uid(100 + number), segment_id=uid(2), operation_id=uid(300 + number),
             status="completed" if number == 1 else "failed")
        for number in (1, 2, 4)
    ])
    assert chart.history(value, uid(3), 1, offset=1, limit=1) == dict(
        total=3, offset=1, attempts=[page["attempts"][1]],
    )
    assert chart.history(value, uid(3), 1, offset=3) == dict(total=3, offset=3, attempts=[])
    assert chart.history(value, uid(3), 1, offset=10**6) == dict(
        total=3, offset=10**6, attempts=[],
    )
    assert chart.history(value, uid(3), 2)["attempts"][0]["id"] == uid(103)
    assert chart.history(value, uid(4), 1)["attempts"][0]["id"] == uid(105)
    page["attempts"][0]["status"] = "local edit"
    page["attempts"].clear()
    assert value == original
    assert chart.history(value, uid(3), 1)["total"] == 3


@pytest.mark.parametrize("cell,offset,limit", [
    (True, 0, 50), (1.0, 0, 50), (0, 0, 50), (3, 0, 50),
    (1, True, 50), (1, 0.0, 50), (1, -1, 50),
    (1, 0, True), (1, 0, 1.0), (1, 0, 0), (1, 0, 51),
])
def test_history_refuses_invalid_cell_and_pagination(cell, offset, limit):
    with pytest.raises(ValueError, match="valid cell, nonnegative offset and page size 1–50"):
        chart.history(state(), uid(3), cell, offset=offset, limit=limit)


@pytest.mark.parametrize("pass_id", [None, True, "", uid(900)])
def test_history_requires_an_explicit_known_pass(pass_id):
    with pytest.raises(ValueError, match="Unknown chart pass"):
        chart.history(state(), pass_id, 1)


def test_decision_details_copy_full_pending_context_reference_and_placement_packets():
    value = complete(begin(chart.set_reference(state(), reference())))
    value = chart.start_pass(value, pass_id=uid(4))
    value = chart.set_reference(value, dict(reference(), meaning="Second synthetic reference"))
    proposed = chart.propose_context(value, context(phase="intended", options={"averages": 2}))
    for value in (proposed, pending_position(prior=True)):
        original = deepcopy(value)
        packet = chart.decision_details(value)
        assert packet == dict(
            pending_context=value["pending_context"],
            passes=[dict(
                id=item["id"], context=item["context"], reference=item["reference"],
                pending_position=item["pending_position"],
            ) for item in value["passes"]],
        )
        if packet["pending_context"] is not None:
            packet["pending_context"]["proposed"]["material"]["options"]["value"] = {}
        for item in packet["passes"]:
            item["context"]["material"]["options"]["value"]["averages"] = 99
            item["reference"]["meaning"] = "local edit"
            if item["pending_position"] is not None:
                item["pending_position"]["check"]["reason"] = "local edit"
        packet["passes"].reverse()
        assert value == original
        assert chart.decision_details(value)["passes"][0]["id"] == uid(3)


@pytest.mark.parametrize("query", ["history", "decision_details"])
def test_shared_queries_refuse_changed_terminal_state_before_disclosing_packets(query):
    value = complete(begin(state()))
    value["passes"][0]["attempts"][0]["reason"] = "Forged terminal reason"
    with pytest.raises(ValueError, match="Terminal chart outcome changed"):
        if query == "history":
            chart.history(value, uid(3), 1)
        else:
            chart.decision_details(value)


@pytest.mark.parametrize("field", [
    "status", "reason", "result_status", "result_reason", "readings", "accepted_context",
    "position_check",
])
def test_terminal_digest_rejects_changed_retained_terminal_facts(field):
    value = complete(begin(state()))
    attempt = value["passes"][0]["attempts"][0]
    changed = {
        "status": "failed",
        "reason": "changed retained reason",
        "result_status": "cancelled",
        "result_reason": "changed original reason",
        "readings": [dict(refs(value)[0], observation_id="changed-native-reading")],
        "accepted_context": context(options={"averages": 2}),
        "position_check": dict(available=False, warning=False, reason="changed check"),
    }
    attempt[field] = changed[field]
    with pytest.raises(ValueError, match="Terminal chart outcome changed"):
        chart.validate_state(value)


def test_unresolved_terminal_digest_retains_original_outcome_for_idempotent_replay():
    value = complete(begin(state()), facts=context(units="cd/m2"))
    attempt = value["passes"][0]["attempts"][0]
    assert attempt["status"] == "unresolved" and attempt["result_status"] == "completed"
    assert attempt["result_reason"] == "Synthetic terminal outcome"
    assert attempt["reason"] != attempt["result_reason"]
    assert complete(value, facts=context(units="cd/m2")) == value
    terminal = {key: attempt[key] for key in (
        "result_status", "result_reason", "status", "reason", "readings",
        "accepted_context", "position_check",
    )}
    expected = hashlib.sha256(json.dumps(
        terminal, ensure_ascii=True, sort_keys=True, allow_nan=False, separators=(",", ":")
    ).encode("ascii")).hexdigest()
    assert attempt["terminal_sha256"] == expected


def test_compact_pending_summary_preserves_token_and_excludes_large_science_packets():
    marker = "large-science-packet-" * 8192
    frozen = dict(reference(), retained_science=marker)
    value = chart.set_reference(state(), frozen)
    check = dict(warning(), retained_science=marker, reason="reason-" * 1024)
    value = complete(begin(value), check=check)
    summary = chart.view(value)
    assert marker not in json.dumps(summary) and len(json.dumps(summary)) < 8192
    pending = value["passes"][0]["pending_position"]
    assert summary["pending_position"]["token"] == pending["token"]
    assert summary["pending_position"]["cell"] == 1
    assert len(summary["pending_position"]["reason"]) == 1024
    assert summary["reference"] == dict(qualified=True, digest="a" * 64)
    assert chart.attempt_details(value, uid(101))["position_check"] == check
    returned = complete(begin(state()), facts=context(options={"large_metadata": marker}))
    compact = chart.view(returned)
    assert marker not in json.dumps(compact)
    assert compact["pending_context"]["difference_count"] == 1
    assert compact["pending_context"]["requires_decision"] is True


def test_metadata_only_sources_enforce_source_cap_unique_segments_and_immediate_parent():
    value = continue_chart(state())
    with pytest.raises(ValueError, match="fresh batch segment"):
        payload, digest = accepted_source(value)
        chart.continue_state(
            value, context(), segment_id=uid(2), source_id="ancestor-reuse",
            source_bytes=payload, source_sha256=digest,
        )
    for mutation, message in (
        (lambda item: item["sources"].append(deepcopy(item["sources"][0])), "unique"),
        (lambda item: item["sources"][0].update(sha256="g" * 64), "SHA-256"),
        (lambda item: item["continuation"].update(source_segment_id=uid(500)), "immediate"),
    ):
        changed = deepcopy(value)
        mutation(changed)
        with pytest.raises(ValueError, match=message):
            chart.validate_state(changed)
    bounded = state()
    bounded["sources"] = [
        dict(
            source_id=f"synthetic-metadata-{number}", segment_id=uid(2000 + number), sha256="a" * 64
        )
        for number in range(64)
    ]
    bounded["continuation"] = dict(
        source_segment_id=uid(2063), source_pass_id=uid(3), action="continue"
    )
    payload, digest = accepted_source(bounded)
    with pytest.raises(ValueError, match="64 source segments"):
        chart.continue_state(
            bounded, context(), segment_id=uid(3000), source_id="synthetic-cap",
            source_bytes=payload, source_sha256=digest,
        )


def test_complete_pass_continuation_preserves_valid_cell_and_full_selected_coverage():
    source = complete(begin(state(rows=1, columns=1)))
    linked = continue_chart(source)
    assert linked["current_cell"] == 1 and chart.view(linked)["coverage"] == 1
    assert linked["passes"] == source["passes"]
    assert linked["passes"][0]["attempts"][0]["readings"] == refs(source)


def test_frozen_reference_cannot_be_replaced_or_cleared_and_exact_replay_is_idempotent():
    frozen = chart.set_reference(state(), reference())
    for replacement in (dict(reference(), digest="b" * 64), None):
        with pytest.raises(ValueError, match="reference is frozen"):
            chart.set_reference(frozen, replacement)
    assert chart.set_reference(frozen, reference()) == frozen
    checked = complete(
        begin(frozen), check=dict(available=True, warning=False, reference_digest="a" * 64,
                                 reason="Synthetic nonwarning position check")
    )
    assert chart.set_reference(checked, reference()) == checked
    assert checked["passes"][0]["attempts"][0]["position_check"]["reference_digest"] == "a" * 64
    pending = pending_position()
    assert chart.set_reference(pending, reference()) == pending


def test_frozen_reference_requires_explicit_material_pass_before_first_attempt():
    frozen = chart.set_reference(state(), reference())
    changed = context(phase="intended", units="cd/m2")
    proposed = chart.propose_context(frozen, changed)
    assert proposed["pending_context"] is not None
    assert proposed["passes"][0]["context"] == frozen["passes"][0]["context"]
    accepted = chart.accept_context(proposed, pass_id=uid(4))
    assert accepted["passes"][0]["reference"] == reference()
    assert accepted["passes"][1]["reference"] is None
    replacement = dict(reference(), digest="b" * 64)
    updated = chart.set_reference(accepted, replacement)
    assert updated["passes"][1]["reference"] == replacement


def test_explicit_same_context_pass_supports_measured_run_then_frozen_comparison_reference():
    measured = complete(begin(state(rows=1, columns=1)))
    comparison = chart.start_pass(measured, pass_id=uid(4), context=context(phase="intended"))
    assert comparison["passes"][0] == measured["passes"][0]
    assert comparison["passes"][1]["attempts"] == []
    assert comparison["passes"][1]["selected_attempts"] == [None]
    assert comparison["passes"][1]["reference"] is None
    assert comparison["current_cell"] == 1 and chart.view(comparison)["coverage"] == 0
    assert chart.view(comparison, pass_id=uid(3))["coverage"] == 1
    frozen_reference = dict(reference(), synthetic_selected_source=uid(101))
    frozen = chart.set_reference(comparison, frozen_reference)
    done = complete(begin(frozen, 2), 2, check=dict(
        available=True, warning=False, reference_digest="a" * 64,
        reason="Synthetic comparison against retained measured run",
    ))
    assert chart.view(done)["coverage"] == 1
    assert done["passes"][1]["reference"] == frozen_reference
    assert chart.attempt_details(done, uid(101), pass_id=uid(3))["readings"] == refs(measured)
    with pytest.raises(ValueError, match="fresh pass identity"):
        chart.start_pass(done, pass_id=uid(4))
    with pytest.raises(ValueError, match="still active"):
        chart.start_pass(begin(frozen, 2), pass_id=uid(5))
    with pytest.raises(ValueError, match="Keep or Retry"):
        chart.start_pass(pending_position(), pass_id=uid(5))
    proposed = chart.propose_context(done, context(phase="intended", units="cd/m2"))
    with pytest.raises(ValueError, match="pending material context"):
        chart.start_pass(proposed, pass_id=uid(5))
    preserved = chart.start_pass(done, pass_id=uid(5))
    assert preserved["passes"][-1]["context"] == done["passes"][-1]["context"]
