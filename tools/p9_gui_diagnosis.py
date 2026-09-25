"""GUI INTERACTION DIAGNOSIS harness (no production code change).

Reproduces from the REAL entry point (ui/desktop/run_windows.py launch
path: DesktopGateway -> WorkstationModel.start -> Shell) and exercises
every interaction programmatically with real Tk events, capturing:
callback exceptions (tk report_callback_exception), binding behavior,
state changes, refresh behavior, event-loop liveness, blocking timings.
"""
from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CAPTURED: list = []


def instrument_tk(shell):
    """Capture every Tk callback exception instead of losing it."""
    def report(exc, val, tb):
        CAPTURED.append("".join(traceback.format_exception(exc, val, tb)))
    shell.report_callback_exception = report
    return report


def timed(label, fn):
    start = time.perf_counter()
    try:
        result = fn()
        ms = (time.perf_counter() - start) * 1000
        print(f"{label:38s} {ms:9.1f} ms  -> {str(result)[:80]}")
        return result, ms
    except Exception as error:
        ms = (time.perf_counter() - start) * 1000
        print(f"{label:38s} {ms:9.1f} ms  RAISED {error}")
        CAPTURED.append(f"{label}: {traceback.format_exc()}")
        return None, ms


def main() -> int:
    print("=== startup (real launch path, timed) ===")
    t0 = time.perf_counter()
    from platform.api.desktop_gateway import DesktopGateway
    from ui.desktop.shell import Shell
    from ui.desktop.viewmodels import WorkstationModel
    import importlib
    t_import = (time.perf_counter() - t0) * 1000
    print(f"imports                                  {t_import:9.1f} ms")

    _, ms_gw = timed("DesktopGateway.__init__",
                     lambda: DesktopGateway(environment="SIMULATION"))
    gateway = DesktopGateway(environment="SIMULATION")
    model = WorkstationModel(gateway)
    timed("model.start()", model.start)

    shell = Shell(model)
    instrument_tk(shell)
    print(f"shell constructed in {(time.perf_counter()-t0)*1000:.0f} ms "
          "cumulative; session =", model.session,
          "| state =", model.state.value)

    # ---- event loop liveness (RC-G check) ----
    print("\n=== event loop liveness ===")
    pumped = []
    shell.after(10, lambda: pumped.append("after-fired"))
    shell.update()
    print("after() callback fired:", bool(pumped))
    shell.geometry("900x600+10+10")
    shell.update()
    print("resize processed:", shell.winfo_width(), "x", shell.winfo_height())
    shell.iconify(); shell.update(); shell.deiconify(); shell.update()
    print("minimize/restore processed")

    # ---- keyboard bindings (real events) ----
    print("\n=== keyboard events ===")
    shell.event_generate("<Control-k>")
    shell.update()
    open_tops = [w for w in shell.winfo_children()
                 if isinstance(w, __import__("tkinter").Toplevel)]
    print("Ctrl+K opened a Toplevel:", len(open_tops) > 0)
    for top in open_tops:
        top.destroy()

    # ---- navigation buttons: real click events ----
    print("\n=== A-region navigation (real Button-1 events) ===")
    for name, button in shell.nav_buttons.items():
        before = model.navigation
        button.event_generate("<Enter>")
        shell.update()
        button.event_generate("<Button-1>")
        button.event_generate("<ButtonRelease-1>")
        shell.update()
        button.invoke()  # deterministic path too
        shell.update()
        changed = model.navigation != before or \
            model.navigation == name
        status = "state=" + model.navigation
        print(f"  {name:14s} click-> {status} "
              f"{'(navigation target applied)' if model.navigation == name else '(nav unchanged)'}")

    # ---- G-region action buttons without login ----
    print("\n=== G-region actions without login (expected BLOCKED) ===")
    for slave in shell.grid_slaves():
        pass
    g_frame = None
    for w in shell.winfo_children():
        for child in w.winfo_children():
            for grand in child.winfo_children():
                if isinstance(grand, __import__("tkinter").ttk.LabelFrame) \
                        and "G - Action" in grand.cget("text"):
                    g_frame = grand
    if g_frame is None:
        print("  G frame not found!")
    else:
        buttons = [w for w in g_frame.winfo_children()
                   if isinstance(w, __import__("tkinter").ttk.Button)]
        print(f"  found {len(buttons)} action buttons")
        for button in buttons:
            label = button.cget("text")
            before_exceptions = len(CAPTURED)
            button.invoke()
            shell.update()
            new_exc = CAPTURED[before_exceptions:]
            # a safety block shows a modal messagebox - destroy leftovers
            for w in shell.winfo_children():
                if isinstance(w, __import__("tkinter").Toplevel):
                    print(f"    {label}: modal shown "
                          f"(safety block) -> destroying")
                    w.destroy()
            print(f"  {label:16s} exceptions={len(new_exc)} "
                  f"action_state={dict(model.action_states)}")

    # ---- search ----
    print("\n=== global search ===")
    shell.search_entry.insert(0, "EURUSD")
    shell.search_entry.event_generate("<Return>")
    shell.update()
    ctx = model.context_stack.current
    print("context after search:", ctx.kind.value if ctx else None,
          ctx.object_id if ctx else None)

    # ---- inspector / blotter / render with symbol selected ----
    print("\n=== select symbol -> inspector/blotter/E/F ===")
    model.select_symbol("EURUSD")
    shell._render()
    shell.update()
    print("inspector text head:",
          shell.c_text.get("1.0", "1.end")[:60])
    print("blotter rows:", len(model.blotter_rows()))
    print("E pane head:", shell.e_text.get("1.0", "1.end")[:50])
    print("F pane head:", shell.f_text.get("1.0", "1.end")[:50])

    # ---- periodic refresh (the 2s loop) ----
    print("\n=== periodic refresh loop ===")
    before = len(CAPTURED)
    shell._periodic_refresh()
    shell.update()
    print("exceptions during refresh:", len(CAPTURED) - before)

    # ---- login then retry actions ----
    print("\n=== login -> actions ===")
    model.login("trader", "synthetic-trader-desktop-secret")
    print("session:", model.session["role"],
          "| state:", model.state.value)
    result = model.submit_order("EURUSD", "BUY", "0.1")
    print("submit_order:", result["state"], result["reasons"][:1])
    shell._render()
    shell.update()
    print("blotter rows after order:", len(model.blotter_rows()))

    # ---- report ----
    print("\n=== captured callback exceptions:",
          len(CAPTURED), "===")
    for trace in CAPTURED[:10]:
        print("-" * 60)
        print(trace[:1200])

    shell.destroy()
    gateway.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
