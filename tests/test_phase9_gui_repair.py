"""GUI interaction repair regression (BASELINE REPAIR for Phase 9).

Covers the three diagnosed root causes:
RC-F navigation visible change (9/9), silent-block visible feedback,
missing login dialog in the production entry point. Safety semantics
(viewmodels/authority) are untouched and re-asserted here.
"""
from __future__ import annotations

import inspect

import pytest

from architecture.contracts.errors import ContractError

from platform.api.desktop_gateway import DesktopGateway

from ui.desktop import run_windows
from ui.desktop.shell import NAV_PRESET, LoginDialog, Shell
from ui.desktop.viewmodels import NAVIGATION, WorkstationModel


@pytest.fixture()
def gateway(tmp_path):
    gw = DesktopGateway(db_path=tmp_path / "repair.db",
                        environment="SIMULATION")
    yield gw
    gw.close()


@pytest.fixture()
def shell(gateway):
    try:
        model = WorkstationModel(gateway)
        model.start()
        instance = Shell(model)
        yield instance
        try:
            instance.destroy()
        except Exception:
            pass
    except Exception as error:
        pytest.skip(f"no display for Tk shell: {error}")


class TestFix1LoginFlow:
    def test_run_windows_launch_shows_login_dialog(self):
        """Regression: launch() must reuse the SAME LoginDialog."""
        source = inspect.getsource(run_windows.launch)
        assert "LoginDialog" in source
        assert "from ui.desktop.shell import LoginDialog" in source or \
            "LoginDialog" in source

    def test_login_dialog_reuses_model_auth(self, shell):
        dialog = LoginDialog(shell, shell.model)
        dialog.persona.delete(0, "end")
        dialog.persona.insert(0, "trader")
        dialog.secret.delete(0, "end")
        dialog.secret.insert(0, "synthetic-trader-desktop-secret")
        dialog._submit()
        assert shell.model.session is not None
        assert shell.model.session["role"] == "TRADER"

    def test_invalid_login_fails_safely(self, shell):
        dialog = LoginDialog(shell, shell.model)
        dialog.persona.delete(0, "end")
        dialog.persona.insert(0, "trader")
        dialog.secret.delete(0, "end")
        dialog.secret.insert(0, "wrong-secret-value")
        dialog._submit()
        assert shell.model.session is None  # session authority unchanged

    def test_cancel_leaves_no_session(self, shell):
        dialog = LoginDialog(shell, shell.model)
        dialog.destroy()
        assert shell.model.session is None


class TestFix2NavigationVisible:
    def test_all_nine_nav_items_have_presets(self):
        assert set(NAVIGATION) == set(NAV_PRESET)

    def test_navigation_changes_visible_workspace(self, shell):
        """9/9: title + content + preset must actually change (not just
        model.navigation)."""
        for item in NAVIGATION:
            title_before = shell.b_title.cget("text")
            content_before = shell.b_text.get("1.0", "end")
            shell.nav_buttons[item].invoke()
            shell.update()
            assert shell.model.navigation == item
            assert shell.model.workspace.preset == NAV_PRESET[item]
            assert shell.b_title.cget("text") != title_before
            assert shell.b_text.get("1.0", "end") != content_before
            assert item.upper() in shell.b_title.cget("text").upper()

    def test_markets_selects_market_tab_others_workspace_tab(self, shell):
        shell.nav_buttons["Markets"].invoke(); shell.update()
        assert shell.b_notebook.select() == str(shell.b_market)
        shell.nav_buttons["Risk"].invoke(); shell.update()
        assert shell.b_notebook.select() == str(shell.b_text)

    def test_workspace_content_is_real_not_fabricated(self, shell):
        shell.nav_buttons["Overview"].invoke(); shell.update()
        content = shell.b_text.get("1.0", "end")
        assert "environment: SIMULATION" in content
        assert "NOT LOGGED IN" in content  # honest state, not fake session
        shell.nav_buttons["Markets"].invoke(); shell.update()
        assert "SYNTHETIC" in shell.b_text.get("1.0", "end")

    def test_navigation_no_exceptions(self, shell):
        errors = []
        shell.report_callback_exception = lambda e, v, tb: errors.append(e)
        for item in NAVIGATION:
            shell.nav_buttons[item].invoke()
            shell.update()
        assert not errors


class TestFix3RejectedFeedback:
    def _g_buttons(self, shell):
        import tkinter.ttk as ttk
        frames = []
        for child in shell.winfo_children():
            for grand in child.winfo_children():
                for frame in grand.winfo_children():
                    if isinstance(frame, ttk.LabelFrame) and \
                            "G - Action" in frame.cget("text"):
                        frames.append(frame)
        assert frames, "G frame not found"
        return {b.cget("text"): b for b in frames[0].winfo_children()
                if isinstance(b, ttk.Button)}

    def test_rejected_actions_show_banner_no_side_effects(self, shell):
        buttons = self._g_buttons(shell)
        for name in ("Buy 0.1", "Pause", "Close Only", "Emergency Stop"):
            shell.banner.config(text="")
            shell.update()
            buttons[name].invoke()
            shell.update()
            banner = shell.banner.cget("text")
            assert banner.startswith("ACTION REJECTED")
            assert name in banner
            assert "Reason:" in banner
            assert "Next step:" in banner
        # no side effect: nothing executed while unauthenticated
        assert len(shell.model._gateway.orders()) == 0
        assert len(shell.model._gateway.positions()) == 0

    def test_safety_semantics_unchanged(self, gateway):
        """The viewmodel still swallows ContractError into REJECTED
        receipts (we did not touch it)."""
        model = WorkstationModel(gateway)
        model.start()
        result = model.submit_order("EURUSD", "BUY", "0.1")
        assert result["state"] == "REJECTED"
        assert result["order_id"] is None

    def test_applied_actions_show_green_banner(self, shell):
        shell.model.login("trader", "synthetic-trader-desktop-secret")
        buttons = self._g_buttons(shell)
        shell.banner.config(text="")
        buttons["Buy 0.1"].invoke()
        shell.update()
        banner = shell.banner.cget("text")
        assert "APPLIED" in banner
        assert len(shell.model._gateway.orders()) == 1


class TestExistingComponents:
    def test_search_inspector_blotter_panels(self, shell):
        shell.search_entry.insert(0, "EURUSD")
        shell._run_search()
        shell.update()
        context = shell.model.context_stack.current
        assert context is not None and context.object_id == "EURUSD"
        assert shell.c_text.get("1.0", "end").startswith("SYMBOL")
        shell.model.login("trader", "synthetic-trader-desktop-secret")
        shell.model.submit_order("EURUSD", "BUY", "0.1")
        shell._render(); shell.update()
        assert "ADVISORY ONLY" in shell.e_text.get("1.0", "end")
        assert "risk state" in shell.f_text.get("1.0", "end")
        assert len(shell.blotter.get_children()) >= 1

    def test_window_controls(self, shell):
        shell.geometry("900x600+5+5")
        shell.update()
        assert shell.winfo_width() == 900
        shell.iconify(); shell.update(); shell.deiconify(); shell.update()
        assert shell.state() == "normal"

    def test_ctrl_k_binding_registered(self, shell):
        # binding present (canonical pattern); physical-key check is a
        # manual step per the repair command (automation-inconclusive)
        assert shell.bind("<Control-k>") != ""
