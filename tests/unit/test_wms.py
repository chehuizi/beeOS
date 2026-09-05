"""WMSBox 7 adapters + 7 ops + domain service manifest 测试。"""

import pytest

from wms import MANIFEST, WORKFLOW, adapters, list_steps, run_step


# === Manifest（领域服务设计）===

class TestManifest:
    def test_box_type(self):
        assert MANIFEST["box_type"] == "wms"

    def test_has_7_entities(self):
        assert len(MANIFEST["entities"]) == 7
        # 关键实体
        for e in ("Item", "Inventory", "InboundOrder", "OutboundOrder"):
            assert e in MANIFEST["entities"]

    def test_has_6_domain_services(self):
        assert len(MANIFEST["services"]) == 6
        # 6 个领域服务
        service_names = [s["name"] for s in MANIFEST["services"]]
        for s in ("ReceivingService", "PutAwayService", "InventoryService",
                  "PickingService", "PackingService", "ShippingService"):
            assert s in service_names

    def test_has_7_operations(self):
        assert len(MANIFEST["operations"]) == 7
        op_names = [op["name"] for op in MANIFEST["operations"]]
        for op in ("query", "receive", "put_away", "count", "pick", "pack", "ship"):
            assert op in op_names

    def test_each_operation_links_to_service(self):
        """每个 operation 必须关联到某个 service。"""
        service_names = {s["name"] for s in MANIFEST["services"]}
        for op in MANIFEST["operations"]:
            assert op["service"] in service_names, \
                f"Operation {op['name']} references unknown service {op['service']}"


# === Adapters（数据工具）===

class TestAdapters:
    """7 个 hardcoded adapter 的 schema 验证。"""

    def test_query_inventory_returns_records(self):
        rows = adapters.query_inventory(period="2026-07")
        assert len(rows) >= 5
        for r in rows:
            assert "sku" in r and "location" in r and "quantity" in r

    def test_query_inbound_orders(self):
        rows = adapters.query_inbound_orders(period="2026-07")
        assert len(rows) >= 1
        for r in rows:
            assert "po_id" in r and "supplier" in r and "lines" in r

    def test_query_outbound_orders(self):
        rows = adapters.query_outbound_orders(period="2026-07")
        assert len(rows) >= 1
        for r in rows:
            assert "so_id" in r and "customer" in r and "lines" in r

    def test_cycle_count_reports_discrepancies(self):
        rows = adapters.cycle_count(period="2026-07")
        assert len(rows) >= 1
        for r in rows:
            assert "location" in r and "sku" in r
            assert "system_qty" in r and "counted_qty" in r

    def test_generate_pick_list(self):
        rows = adapters.generate_pick_list(period="2026-07")
        assert len(rows) >= 1
        for r in rows:
            assert "pick_id" in r and "so_id" in r and "lines" in r

    def test_pack_collect(self):
        rows = adapters.pack_collect(period="2026-07")
        assert len(rows) >= 1
        for r in rows:
            assert "package_id" in r and "weight_kg" in r

    def test_ship_request(self):
        rows = adapters.ship_request(period="2026-07", approver="x@x.com")
        assert len(rows) >= 1
        for r in rows:
            assert "shipment_id" in r and "carrier" in r and "tracking_no" in r


# === Workflow（7 ops 默认流程）===

class TestWorkflow:
    """7 步默认工作流 + run_step 入口。"""

    def test_workflow_has_7_ops(self):
        assert len(WORKFLOW) == 7
        assert list_steps() == [
            "query", "receive", "put_away", "count",
            "pick", "pack", "ship",
        ]

    def test_each_op_has_name_tool_description(self):
        for op in WORKFLOW:
            assert "name" in op
            assert "tool" in op
            assert "description" in op

    def test_run_step_query(self):
        out = run_step("query", {"period": "2026-07"}, {})
        assert "total_skus" in out
        assert "total_quantity" in out
        assert len(out["items"]) > 0

    def test_run_step_receive(self):
        out = run_step("receive", {"period": "2026-07"}, {})
        assert "pending_count" in out
        assert "orders" in out
        assert len(out["orders"]) > 0

    def test_run_step_pick_uses_so_id(self):
        out = run_step("pick", {"period": "2026-07"}, {})
        assert "pick_lists" in out
        assert "total_qty" in out
        # 每个 pick_list 都关联一个 SO
        for pl in out["details"]:
            assert pl["so_id"].startswith("SO-")

    def test_run_step_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown step"):
            run_step("nonexistent", {"period": "2026-07"}, {})


# === Schema ===

class TestSchema:
    """Pydantic schema 验证（数据契约）。"""

    def test_item_accepts_valid(self):
        from wms.schema import Item
        item = Item(sku="SKU-001", name="电容", category="electronics",
                    unit="pcs", weight_kg=0.01, volume_m3=0.0001)
        assert item.sku == "SKU-001"
        assert item.weight_kg == 0.01

    def test_inventory_with_lot(self):
        from wms.schema import Inventory
        inv = Inventory(sku="SKU-001", location="A-01-1", quantity=100,
                        lot="L240101", status="available")
        assert inv.lot == "L240101"
        assert inv.status == "available"

    def test_inbound_order_structure(self):
        from wms.schema import InboundOrder
        order = InboundOrder(
            po_id="PO-001", supplier="ABC", expected_at="2026-07-15",
            lines=[{"sku": "SKU-001", "expected_qty": 100, "received_qty": 0}],
            status="pending",
        )
        assert order.po_id == "PO-001"
        assert len(order.lines) == 1

    def test_shipment_carrier_required(self):
        from wms.schema import Shipment
        s = Shipment(shipment_id="SHP-001", so_id="SO-001",
                     carrier="顺丰", tracking_no="SF123", status="label_created")
        assert s.carrier == "顺丰"
