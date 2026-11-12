"""Step 23: contract audit gate."""

from __future__ import annotations

from opal_device.contract_audit import audit, contract_operations


def test_all_contract_operations_mounted():
    report = audit()
    assert report["total_operations"] == 77
    assert report["missing"] == []
    assert report["extra_paths"] == []


def test_operation_ids_present():
    ops = contract_operations()
    assert all(op["operation_id"] for op in ops)
    assert len({(op["method"], op["path"]) for op in ops}) == len(ops)
