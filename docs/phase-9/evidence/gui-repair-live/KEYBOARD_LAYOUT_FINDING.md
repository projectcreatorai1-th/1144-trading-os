# Keyboard Layout Finding — Ctrl+K (FIX 4 environment)

**Verdict: Ctrl+K = PASS with real input under en-US layout normalization.
Root cause of all prior failures is MACHINE ENVIRONMENT, not an app defect.
The production binding is untouched.**

## Evidence chain (probes 1-7, temp scripts removed after verification)

| # | Experiment | Result |
|---|-----------|--------|
| 1 | minimal Tk, `event_generate` Control-k | no fire (original Phase 9 diagnosis: INCONCLUSIVE) |
| 2 | minimal Tk + real `keybd_event` Ctrl+K / bare 'k', verified foreground | no fire |
| 3 | + explicit `focus_set()` (focus_get = entry) | no fire |
| 4 | activation matrix: attach-activate -> VK ENTER | **ENTER fired** (keys DO reach Tk) |
| 4 | real click -> VK Ctrl+K | **CTRL+K not fired** |
| 5 | `RegisterHotKey(Ctrl+K / Alt+K / Ctrl+Shift+K)` | all FREE — no system-wide hotkey claim |
| 5 | key matrix -> Tk `<KeyPress>` | `KEY:??:state` for k, x, ctrl+k, ctrl+f, shift+k, alt+k; F1 -> `KEY:F1` |
| 6 | target thread layout | **0x41E041E = Thai**; ActivateKeyboardLayout cannot be set cross-thread |
| 7 | `WM_INPUTLANGCHANGEREQUEST` -> en-US (0x4090409) -> real `keybd_event` Ctrl+K | **CTRL_K_FIRED**; layout restored to Thai |

## Root cause

The Windows keyboard layout on this machine is **Thai (0x041E)**. `ToUnicode(VK 'K')`
yields a Thai character, so Tk assigns letter keys the keysym `??`. The
`<Control-k>` binding can never match — **for synthesized AND physical input
alike**: a real keyboard pressed under the Thai layout fails identically on
this machine. Named keys (Enter, F1, Escape) map fine, which is why the login
dialog (Enter) worked throughout.

## Why this is not papered over

- The binding `self.bind(PALETTE_SHORTCUT, self._open_palette)` (`shell.py`) is
  the canonical pattern and fires correctly the moment Tk receives keysym 'k'
  with Control state (probe 7).
- Tk's keysym-based bindings cannot match by virtual-key accelerator the way
  Chrome/Notepad do; any non-Latin active layout hits the same limitation.
- **Documented follow-up risk (not implemented, per the no-binding-change
  rule):** a user working with the Thai (or any non-Latin) layout active
  cannot invoke Ctrl+K. Candidate future mitigations: additional
  `<Control-K>` binding, VK-aware binding, or a menu/button entry point.
  This finding is the evidence for that future decision.

## Driver-side test method (tools/p9_live_gui_test.py)

`kb_vk_combo()` mirrors exactly what a human tester does: switch the target
window's input language to en-US (language-bar semantics via
`WM_INPUTLANGCHANGEREQUEST`), send a real `keybd_event` Ctrl+K, then restore
the original layout. Every layout change is logged in
`live_gui_report.json` (`layout:*` entries: 0x41e041e -> 0x4090409 -> restored).
