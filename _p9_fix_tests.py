"""One-shot: REJECTED receipts + test fixes (deleted after use)."""
from pathlib import Path

v = Path("ui/desktop/viewmodels.py")
src = v.read_text(encoding="utf-8")

old_start = "        action = {\"pause\": \"pause\", \"close_only\": \"close_only\","
index = src.index(old_start)
end = src.index("return {\"state\": \"UNKNOWN\", \"reasons\": (\"unhandled\",)}",
                index) + len(
    "{\"state\": \"UNKNOWN\", \"reasons\": (\"unhandled\",)}")
new_block = '''action = {"pause": "pause", "close_only": "close_only",
                  "emergency_stop": "emergency_stop"}.get(command_id)
        if action:
            try:
                receipt = self._gateway.act(
                    action, {"reason": "command palette"})
            except ContractError as error:
                self.action_states[command_id] = "REJECTED"
                return {"state": "REJECTED", "reasons": (str(error),)}
            self.action_states[command_id] = receipt.state
            return {"state": receipt.state,
                    "reasons": receipt.reasons}
        return {"state": "UNKNOWN", "reasons": ("unhandled",)}'''
src = src[:index] + new_block + src[end:]

sub_start = src.index("        key = f\"order:{symbol}:{side}:{quantity}\"")
sub_end = src.index("self.action_states[key] = receipt.state", sub_start)
new_sub = '''key = f"order:{symbol}:{side}:{quantity}"
        self.action_states[key] = "REQUESTED"
        try:
            receipt = self._gateway.act(
                "submit_order", {"symbol": symbol, "side": side,
                                 "quantity": quantity})
        except ContractError as error:
            self.action_states[key] = "REJECTED"
            self.push_notification(
                category="EXECUTION",
                title=f"order REJECTED ({symbol} {side} {quantity})",
                severity=NotificationSeverity.WARNING)
            return {"state": "REJECTED", "reasons": (str(error),),
                    "order_id": None, "position_id": None}
'''
src = src[:sub_start] + new_sub + src[sub_end:]
v.write_text(src, encoding="utf-8")

t = Path("tests/test_phase9_core.py")
src = t.read_text(encoding="utf-8")
replacements = [
    ('''    def test_pause_blocks_new_orders(self, gateway):
        gateway.act("pause", {"reason": "operator"})''',
     '''    def test_pause_blocks_new_orders(self, gateway):
        gateway.login("risk_manager", "synthetic-risk-desktop-secret")
        gateway.act("pause", {"reason": "operator"})
        gateway.logout()
        gateway.login("trader", "synthetic-trader-desktop-secret")'''),
    ('    def test_rate_limited_after_burst(self, gateway):',
     '    def test_rate_limited_after_burst(self, gateway):\n'
     '        gateway.login("risk_manager", "synthetic-risk-desktop-secret")'),
    ('    def test_core_why_is_separate(self, gateway):',
     '    def test_core_why_is_separate(self, gateway):\n'
     '        gateway.login("trader", "synthetic-trader-desktop-secret")'),
    ('    def test_search_finds_real_objects(self, gateway):',
     '    def test_search_finds_real_objects(self, gateway):\n'
     '        gateway.login("trader", "synthetic-trader-desktop-secret")'),
    ('''        from architecture.contracts.errors import ContractValidationError
        with pytest.raises(PanelState(panel_id="x", region="Z").__class__()):
            pass
        panel = PanelState(panel_id="x", region="Z")''',
     '''        from architecture.contracts.errors import ContractValidationError
        panel = PanelState(panel_id="x", region="Z")'''),
]
for old, new in replacements:
    assert old in src, old[:60]
    src = src.replace(old, new, 1)
t.write_text(src, encoding="utf-8")
print("all fixed")
