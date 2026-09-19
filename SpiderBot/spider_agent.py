from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import cv2

import spider_models
import spider_ollama
from spider_learning import SpiderLearning
from spider_solver import SpiderAction, add_lookahead, generate_actions, state_signature
from spider_state import SpiderState, read_state
from spider_windows import WindowAutomationError, WindowController, frame_difference


class SpiderAgentError(RuntimeError):
    pass


@dataclass
class SpiderConfig:
    hwnd: int | None = None
    model: str = "laya"
    depth: int = 3
    stock_x: float = 0.82
    stock_y: float = 0.78
    learning: bool = True
    action_delay: float = 0.85
    input_mode: str = "background"
    vision_enabled: bool = False
    vision_model: str = ""


class SpiderAgent:
    def __init__(self):
        self.config = SpiderConfig()
        self.learning = SpiderLearning()
        self.controller: WindowController | None = None
        self.last_state: SpiderState | None = None
        self.last_action: dict[str, Any] | None = None
        self.last_error: str | None = None
        self.last_frame = None
        self.last_vision: dict[str, Any] | None = None
        self.last_action_count = 0
        self.phase = "idle"
        self.phase_detail = ""
        self.phase_started_at = time.time()
        self.moves = 0
        self.running = False
        self.game_finished = False
        self.game_won = False
        self.trajectory: list[dict[str, Any]] = []
        self.failed_actions: dict[str, set[str]] = {}
        self.stock_deals_used = 0
        self.stock_failed_clicks = 0
        self._learning_committed = False

    def _set_phase(self, phase: str, detail: str = "") -> None:
        self.phase = phase
        self.phase_detail = detail
        self.phase_started_at = time.time()

    def select_window(self, hwnd: int) -> None:
        self.controller = WindowController(int(hwnd))
        self.config.hwnd = int(hwnd)
        self.last_error = None
        self.last_state = None
        self.last_frame = None
        self.last_vision = None
        self._set_phase("idle", "Fenster ausgewählt")

    def set_model(self, model: str) -> None:
        name = model.strip().lower()
        if name not in ("laya", "typesafe"):
            raise SpiderAgentError("Modell muss 'laya' oder 'typesafe' sein.")
        self.config.model = name

    def set_typesafe_key(self, key: str | None) -> None:
        spider_models.set_typesafe_key(key)

    def set_stock_point(self, x: float, y: float) -> None:
        self.config.stock_x = min(1.0, max(0.0, float(x)))
        self.config.stock_y = min(1.0, max(0.0, float(y)))

    def configure(
        self,
        *,
        depth: int | None = None,
        learning: bool | None = None,
        action_delay: float | None = None,
        input_mode: str | None = None,
        vision_enabled: bool | None = None,
        vision_model: str | None = None,
    ) -> None:
        if depth is not None:
            self.config.depth = max(1, min(int(depth), 5))
        if learning is not None:
            self.config.learning = bool(learning)
        if action_delay is not None:
            self.config.action_delay = max(0.15, min(float(action_delay), 5.0))
        # SpiderBot is intentionally background-only. It must never move the
        # user's real cursor or activate/raise the game window.
        self.config.input_mode = "background"
        if vision_enabled is not None:
            self.config.vision_enabled = bool(vision_enabled)
        if vision_model is not None:
            self.config.vision_model = str(vision_model).strip()

    def reset_episode(self) -> None:
        self.moves = 0
        self.game_finished = False
        self.game_won = False
        self.trajectory = []
        self.failed_actions = {}
        self.stock_deals_used = 0
        self.stock_failed_clicks = 0
        self.last_action = None
        self.last_error = None
        self._learning_committed = False
        self._set_phase("idle", "Neue Partie")

    def _require_controller(self) -> WindowController:
        if self.controller is None:
            raise SpiderAgentError("Zuerst das Windows-Spiel-Fenster auswählen.")
        return self.controller

    def observe(self, use_vision: bool = True) -> tuple[SpiderState, Any]:
        ctl = self._require_controller()
        self._set_phase("capture", "Spielfenster aufnehmen")
        frame = ctl.capture()
        info = ctl.info

        vision_primary = bool(
            use_vision and self.config.vision_enabled and self.config.vision_model
        )
        self._set_phase(
            "read_state",
            "Karten mit UIA lesen; Ollama übernimmt Vision"
            if vision_primary
            else "Karten mit UIA/OCR lesen",
        )
        state = read_state(
            ctl.hwnd,
            frame,
            info.left,
            info.top,
            stock_point=(self.config.stock_x, self.config.stock_y),
            use_ocr=not vision_primary,
        )
        if state.stock_point is not None:
            self.config.stock_x = float(state.stock_point[0])
            self.config.stock_y = float(state.stock_point[1])

        if use_vision:
            self.last_vision = None
            if self.config.vision_enabled and self.config.vision_model:
                try:
                    self._set_phase(
                        "ollama_vision",
                        f"Ollama {self.config.vision_model} liest die Karten",
                    )
                    hint = spider_ollama.analyze_spider(frame, self.config.vision_model)
                    self.last_vision = spider_ollama.apply_hint(state, hint)

                    visible_cards = sum(len(c.cards) for c in state.columns)
                    applied = len(self.last_vision.get("applied_columns") or [])
                    confidence = float(self.last_vision.get("confidence") or 0.0)

                    # If the VLM result is clearly incomplete, fall back to OCR
                    # once for this observation. In the normal successful path
                    # RapidOCR is never initialized/run at all.
                    if visible_cards < 5 or confidence < 0.55:
                        self._set_phase(
                            "ocr_fallback",
                            "Ollama unsicher – einmaliger OCR-Fallback",
                        )
                        fallback = read_state(
                            ctl.hwnd,
                            frame,
                            info.left,
                            info.top,
                            stock_point=(self.config.stock_x, self.config.stock_y),
                            use_ocr=True,
                        )
                        fallback.diagnostics.append(
                            f"Ollama war unvollständig (confidence={confidence:.2f}, "
                            f"angewendete Spalten={applied}); OCR-Fallback verwendet."
                        )
                        state = fallback
                except Exception as exc:
                    self.last_vision = {
                        "model": self.config.vision_model,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                    self._set_phase(
                        "ocr_fallback",
                        "Ollama-Fehler – OCR-Fallback",
                    )
                    state = read_state(
                        ctl.hwnd,
                        frame,
                        info.left,
                        info.top,
                        stock_point=(self.config.stock_x, self.config.stock_y),
                        use_ocr=True,
                    )
                    state.diagnostics.append(
                        "Ollama Vision-Fehler: " + self.last_vision["error"]
                    )

        self.last_state = state
        self.last_frame = frame
        self._set_phase("observed", "Spielzustand gelesen")
        return state, frame

    def _healthy_state(self, state: SpiderState) -> bool:
        visible = sum(len(c.cards) for c in state.columns)
        hidden_cols = sum(1 for c in state.columns if c.hidden_above)
        return visible >= 5 or hidden_cols >= 5

    def _is_win(self, state: SpiderState) -> bool:
        visible = sum(len(c.cards) for c in state.columns)
        hidden = any(c.hidden_above for c in state.columns)
        return visible == 0 and not hidden and not state.stock_available and self.moves > 10

    def _is_stuck(self, state: SpiderState, actions: list[SpiderAction]) -> bool:
        return not actions and not state.stock_available

    def _source_point(self, state: SpiderState, action: SpiderAction) -> tuple[float, float]:
        assert action.source is not None and action.start_index is not None
        col = state.columns[action.source]
        if action.start_index >= len(col.cards):
            raise SpiderAgentError("Quellkarte ist in der erkannten Stellung nicht mehr vorhanden.")
        card = col.cards[action.start_index]

        # x uses the stable tableau-column center. y comes from UIA/OCR.
        x = col.x
        if card.source == "uia" and card.height > state.height * 0.03:
            y = card.y
        else:
            # OCR usually returns the tiny rank glyph near the card's top-left.
            y = card.y + max(12.0, state.height * 0.025)
        return x, y

    def _destination_point(self, state: SpiderState, action: SpiderAction) -> tuple[float, float]:
        assert action.destination is not None
        col = state.columns[action.destination]
        x = col.x
        if col.cards:
            card = col.cards[-1]
            if card.source == "uia" and card.height > state.height * 0.03:
                y = card.y
            else:
                y = card.y + max(22.0, state.height * 0.045)
        else:
            # Empty-column drop zone in Microsoft Spider.
            y = state.height * 0.31
        return x, y

    def _execute(
        self,
        state: SpiderState,
        action: SpiderAction,
        before,
    ) -> tuple[bool, float, Any, str, dict[str, Any]]:
        ctl = self._require_controller()

        start = None
        end = None
        if action.kind == "move":
            start = self._source_point(state, action)
            end = self._destination_point(state, action)

        stock_x, stock_y = (
            state.stock_point
            if state.stock_point is not None
            else (self.config.stock_x, self.config.stock_y)
        )
        stock_client = ctl.normalized_to_client(stock_x, stock_y)
        debug: dict[str, Any] = {"attempts": []}

        # 1) Preferred path: Windows UI Automation accessibility patterns.
        # This performs Invoke/SelectionItem/Legacy actions and never touches
        # the physical mouse or foreground window.
        try:
            self._set_phase("input_uia", f"UIA-Aktion {action.notation()}")
            if action.kind == "deal":
                uia = ctl.uia_invoke_stock(stock_client)
            else:
                assert start is not None and end is not None
                uia = ctl.uia_move(start, end)
            debug["attempts"].append({"mode": "uia", "result": uia})

            time.sleep(self.config.action_delay)
            after = ctl.capture()
            diff = frame_difference(before, after)
            if diff >= 0.0015:
                return True, diff, after, "uia_accessibility", debug
        except Exception as exc:
            debug["attempts"].append({
                "mode": "uia",
                "error": f"{type(exc).__name__}: {exc}",
            })

        # 2) Secondary mouse-free path: background WM_MOUSE messages.
        # Microsoft Solitaire may reject these; if it does, report that fact.
        try:
            self._set_phase("input_background", f"Background-Aktion {action.notation()}")
            if action.kind == "deal":
                ctl.click_normalized(stock_x, stock_y)
            else:
                assert start is not None and end is not None
                ctl.virtual_drag(start, end, duration_ms=620, steps=40)

            time.sleep(self.config.action_delay)
            after = ctl.capture()
            diff = frame_difference(before, after)
            debug["attempts"].append({
                "mode": "background_wm_mouse",
                "screen_difference": round(diff, 6),
            })
            return diff >= 0.0015, diff, after, "background_wm_mouse", debug
        except Exception as exc:
            after = ctl.capture()
            diff = frame_difference(before, after)
            debug["attempts"].append({
                "mode": "background_wm_mouse",
                "error": f"{type(exc).__name__}: {exc}",
            })
            return False, diff, after, "no_mouse_free_input_accepted", debug

    def step(self) -> dict[str, Any]:
        self.last_error = None
        self._set_phase("observe", "Spielzustand erfassen")
        state, before = self.observe(use_vision=True)

        if not self._healthy_state(state):
            raise SpiderAgentError(
                "Die Karten konnten nicht zuverlässig erkannt werden. "
                + " | ".join(state.diagnostics[-3:])
            )

        if self._is_win(state):
            self._finish_learning(True)
            self.game_finished = True
            self.game_won = True
            return self.status()

        self._set_phase("lookahead", f"Legale Züge + Vorausschau Tiefe {self.config.depth}")
        actions = add_lookahead(
            state,
            generate_actions(state),
            self.config.depth,
        )
        self.last_action_count = len(actions)

        sig = state_signature(state)
        banned = self.failed_actions.get(sig, set())
        actions = [a for a in actions if a.notation() not in banned]

        if self.config.learning:
            for action in actions:
                self.learning.enrich(self.config.model, sig, action)

        # The purple-stock color detector is only a hint. The user explicitly
        # calibrates the stock position, and a Spider deal contains at most five
        # stock clicks. If no tableau move exists, probe the calibrated stock
        # point even when vision says "no stock". This prevents false stops.
        if not actions and self.stock_deals_used < 5 and self.stock_failed_clicks < 2:
            actions = [
                SpiderAction(
                    id="deal0",
                    kind="deal",
                    immediate_score=10.0,
                    lookahead_score=10.0,
                    principal_variation=["STOCK"],
                    features={
                        "deal": 1.0,
                        "vision_stock_detected": 1.0 if state.stock_available else 0.0,
                        "calibrated_stock_probe": 1.0,
                    },
                )
            ]

        if self._is_stuck(state, actions):
            raise SpiderAgentError(
                "Keine legalen Tableau-Züge erkannt. Der Stock wurde ebenfalls "
                "mehrfach erfolglos angeklickt oder bereits fünfmal benutzt. "
                "Das ist wahrscheinlich ein Erkennungs-/Kalibrierungsproblem, "
                "nicht automatisch eine verlorene Partie."
            )

        if not actions:
            raise SpiderAgentError("Keine Aktion aus der erkannten Stellung erzeugt.")

        try:
            self._set_phase(
                "decision",
                "TypeSafe/Jev entscheidet" if self.config.model == "typesafe" else "Laya entscheidet",
            )
            selected, model_debug = spider_models.choose(
                self.config.model,
                state,
                actions,
                self.config.depth,
            )
        except spider_models.SpiderModelError as exc:
            raise SpiderAgentError(str(exc)) from exc

        self._set_phase("input", f"Mausfreie Aktion {selected.notation()}")
        changed_pixels, diff, after, input_used, input_debug = self._execute(
            state,
            selected,
            before,
        )

        # Pixel differences alone can be caused by a selection highlight.
        # Verify the actual recognized Spider state before counting a move.
        self._set_phase("verify", "Spielzustand nach Aktion verifizieren")
        try:
            time.sleep(0.12)
            next_state, _ = self.observe(use_vision=False)
            next_sig = state_signature(next_state)
            board_changed = next_sig != sig
        except Exception as exc:
            next_state = None
            next_sig = sig
            board_changed = False
            input_debug["verify_error"] = f"{type(exc).__name__}: {exc}"

        accepted = bool(changed_pixels and board_changed)

        # If UIA only produced a visual selection highlight (or the WM_MOUSE
        # route was rejected), try Spider's keyboard navigation before giving
        # up. This still does not touch the user's real keyboard or mouse.
        if not accepted:
            ctl = self._require_controller()
            self._set_phase(
                "input_keyboard",
                f"Tastatur-Navigation {selected.notation()} ohne echte Eingabegeräte",
            )
            try:
                if selected.kind == "deal":
                    keyboard_result = ctl.keyboard_spider_deal()
                else:
                    assert selected.source is not None
                    assert selected.destination is not None
                    assert selected.start_index is not None
                    visible_count = len(state.columns[selected.source].cards)

                    keyboard_result = ctl.keyboard_spider_move(
                        selected.source,
                        selected.destination,
                        selected.start_index,
                        visible_count,
                        vertical_from_top=True,
                    )

                input_debug.setdefault("attempts", []).append(
                    {"mode": "background_keyboard", "result": keyboard_result}
                )
                time.sleep(self.config.action_delay)

                self._set_phase("verify_keyboard", "Tastatur-Zug verifizieren")
                keyboard_state, keyboard_frame = self.observe(use_vision=False)
                keyboard_sig = state_signature(keyboard_state)
                keyboard_diff = frame_difference(before, keyboard_frame)

                if keyboard_sig != sig:
                    accepted = True
                    board_changed = True
                    diff = keyboard_diff
                    input_used = "background_keyboard"
                    next_state = keyboard_state
                elif selected.kind == "move":
                    # Try the opposite vertical normalization once. Microsoft
                    # Solitaire versions differ in how Up/Down enter a column.
                    keyboard_result_2 = ctl.keyboard_spider_move(
                        selected.source,
                        selected.destination,
                        selected.start_index,
                        visible_count,
                        vertical_from_top=False,
                    )
                    input_debug.setdefault("attempts", []).append(
                        {"mode": "background_keyboard_reverse", "result": keyboard_result_2}
                    )
                    time.sleep(self.config.action_delay)
                    keyboard_state_2, keyboard_frame_2 = self.observe(use_vision=False)
                    keyboard_sig_2 = state_signature(keyboard_state_2)
                    keyboard_diff_2 = frame_difference(before, keyboard_frame_2)
                    if keyboard_sig_2 != sig:
                        accepted = True
                        board_changed = True
                        diff = keyboard_diff_2
                        input_used = "background_keyboard_reverse"
                        next_state = keyboard_state_2
            except Exception as exc:
                input_debug.setdefault("attempts", []).append({
                    "mode": "background_keyboard",
                    "error": f"{type(exc).__name__}: {exc}",
                })

        if selected.kind == "deal":
            if accepted:
                self.stock_deals_used += 1
                self.stock_failed_clicks = 0
            else:
                self.stock_failed_clicks += 1

        if not accepted:
            self.failed_actions.setdefault(sig, set()).add(selected.notation())
            self._set_phase(
                "input_rejected",
                "UIA, Hintergrundmaus und Hintergrund-Tastatur ohne Brettänderung",
            )
            self.last_action = {
                "model": self.config.model,
                "action": selected.to_dict(),
                "accepted": False,
                "screen_difference": round(diff, 6),
                "board_changed": board_changed,
                "input_used": input_used,
                "input_debug": input_debug,
                "stock_deals_used": self.stock_deals_used,
                "stock_failed_clicks": self.stock_failed_clicks,
                "model_debug": model_debug,
                "warning": (
                    "SpiderBot hat UI Automation, Hintergrund-WM_MOUSE und "
                    "Hintergrund-Tastaturnavigation versucht. Die echte Maus "
                    "und Tastatur wurden nicht übernommen. Der erkannte "
                    "Spielzustand hat sich nicht geändert."
                ),
            }
            return self.status()

        self.moves += 1
        if self.config.learning:
            self.learning.record_move(self.config.model)
            self.trajectory.append(
                {
                    "state_sig": sig,
                    "action": selected.notation(),
                    "features": selected.features,
                }
            )

        self.last_action = {
            "model": self.config.model,
            "action": selected.to_dict(),
            "accepted": True,
            "screen_difference": round(diff, 6),
            "board_changed": True,
            "input_used": input_used,
            "input_debug": input_debug,
            "stock_deals_used": self.stock_deals_used,
            "stock_failed_clicks": self.stock_failed_clicks,
            "model_debug": model_debug,
        }

        if next_state is not None and self._is_win(next_state):
            self.game_finished = True
            self.game_won = True
            self._finish_learning(True)

        self._set_phase("idle", "Bereit für nächsten Zug")
        return self.status()

    def _finish_learning(self, won: bool) -> None:
        if self._learning_committed or not self.config.learning:
            return
        self.learning.finish_game(self.config.model, self.trajectory, won)
        self._learning_committed = True

    def frame_jpeg(self) -> bytes:
        ctl = self._require_controller()
        frame = ctl.capture()
        ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
        if not ok:
            raise SpiderAgentError("Screenshot konnte nicht als JPEG kodiert werden.")
        return encoded.tobytes()

    def status(self) -> dict[str, Any]:
        info = self.controller.info.to_dict() if self.controller is not None else None
        return {
            "config": {
                "hwnd": self.config.hwnd,
                "model": self.config.model,
                "depth": self.config.depth,
                "stock_x": round(self.config.stock_x, 4),
                "stock_y": round(self.config.stock_y, 4),
                "learning": self.config.learning,
                "action_delay": self.config.action_delay,
                "input_mode": "uia_then_background_messages",
                "physical_mouse_touched": False,
                "physical_keyboard_touched": False,
                "foreground_window_changed": False,
                "vision_enabled": self.config.vision_enabled,
                "vision_model": self.config.vision_model,
                "stock_deals_used": self.stock_deals_used,
                "stock_failed_clicks": self.stock_failed_clicks,
            },
            "window": info,
            "moves": self.moves,
            "legal_actions_detected": self.last_action_count,
            "game_finished": self.game_finished,
            "game_won": self.game_won,
            "last_error": self.last_error,
            "phase": self.phase,
            "phase_detail": self.phase_detail,
            "phase_elapsed_seconds": round(max(0.0, time.time() - self.phase_started_at), 1),
            "last_action": self.last_action,
            "last_vision": self.last_vision,
            "state": self.last_state.to_dict() if self.last_state else None,
            "laya": spider_models.laya_status(),
            "typesafe": spider_models.typesafe_status(),
            "learning": self.learning.summary(),
            "input_status": self.controller.input_status() if self.controller is not None else {
                "mode": "background_only",
                "physical_mouse_touched": False,
                "foreground_changed": False,
                "target": None,
            },
        }
