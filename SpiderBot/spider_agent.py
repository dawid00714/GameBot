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
from vm_guest_input import input_abort_requested


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
        # Actual input mode is controlled by the Windows controller/startup
        # environment (real host mouse, VM guest, or background fallback).
        self.config.input_mode = "auto"
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

        vision_requested = bool(
            use_vision and self.config.vision_enabled and self.config.vision_model
        )
        self._set_phase("read_state", "Karten mit UIA/FastOCR lesen")
        state = read_state(
            ctl.hwnd,
            frame,
            info.left,
            info.top,
            stock_point=(self.config.stock_x, self.config.stock_y),
            use_ocr=True,
        )
        if state.stock_point is not None:
            self.config.stock_x = float(state.stock_point[0])
            self.config.stock_y = float(state.stock_point[1])

        if use_vision:
            self.last_vision = None
            if vision_requested:
                visible_cards = sum(len(c.cards) for c in state.columns)
                occupied_cols = sum(1 for c in state.columns if c.cards)

                # UI Automation is authoritative and already gives exact card
                # geometry. Do not waste 20-40 seconds asking a VLM to reread it.
                # FastOCR also skips Ollama when it has a clearly usable board.
                vision_needed = not (
                    state.reader == "uia"
                    or (visible_cards >= 8 and occupied_cols >= 7)
                )

                if not vision_needed:
                    self.last_vision = {
                        "model": self.config.vision_model,
                        "skipped": True,
                        "reason": (
                            f"{state.reader} bereits ausreichend: "
                            f"{visible_cards} Karten in {occupied_cols} Spalten"
                        ),
                    }
                    state.diagnostics.append(
                        "Ollama übersprungen: UIA/FastOCR hat die Stellung bereits ausreichend gelesen."
                    )
                else:
                    try:
                        self._set_phase(
                            "ollama_vision",
                            f"Ollama {self.config.vision_model} hilft bei unsicheren Rängen",
                        )
                        hint = spider_ollama.analyze_spider(frame, self.config.vision_model)
                        self.last_vision = spider_ollama.apply_hint(state, hint)
                    except Exception as exc:
                        # Ollama is optional. Never replace a usable OCR/UIA
                        # state merely because the helper model misbehaved.
                        self.last_vision = {
                            "model": self.config.vision_model,
                            "error": f"{type(exc).__name__}: {exc}",
                            "ignored": True,
                        }
                        state.diagnostics.append(
                            "Ollama-Hilfe fehlgeschlagen und wurde ignoriert: "
                            + self.last_vision["error"]
                        )

        self.last_state = state
        self.last_frame = frame
        self._set_phase("observed", "Spielzustand gelesen")
        return state, frame

    def _verify_frame_state(self, frame) -> SpiderState:
        """Read post-action state quickly.

        UIA/FastOCR is always first. Ollama is only consulted when that result
        is visibly incomplete; a VLM failure never invalidates a usable board.
        """
        ctl = self._require_controller()
        info = ctl.info

        state = read_state(
            ctl.hwnd,
            frame,
            info.left,
            info.top,
            stock_point=(self.config.stock_x, self.config.stock_y),
            use_ocr=True,
        )

        if state.stock_point is not None:
            self.config.stock_x = float(state.stock_point[0])
            self.config.stock_y = float(state.stock_point[1])

        if not (self.config.vision_enabled and self.config.vision_model):
            return state

        visible = sum(len(c.cards) for c in state.columns)
        occupied = sum(1 for c in state.columns if c.cards)
        if state.reader == "uia" or (visible >= 8 and occupied >= 7):
            return state

        try:
            self._set_phase(
                "verify_ollama",
                f"Ollama {self.config.vision_model} hilft bei der Verifikation",
            )
            hint = spider_ollama.analyze_spider(frame, self.config.vision_model)
            spider_ollama.apply_hint(state, hint)
        except Exception as exc:
            state.diagnostics.append(
                f"Ollama-Verifikation ignoriert: {type(exc).__name__}: {exc}"
            )
        return state


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

        stock_x, stock_y = (
            state.stock_point
            if state.stock_point is not None
            else (self.config.stock_x, self.config.stock_y)
        )

        debug: dict[str, Any] = {"attempts": []}

        try:
            if action.kind == "deal":
                x, y = ctl.normalized_to_client(stock_x, stock_y)
                self._set_phase("input_drag", "Stock: linke Taste drücken und loslassen")
                result = ctl.held_mouse_click(x, y, hold_ms=110)
                input_used = "held_mouse_click"
            else:
                start = self._source_point(state, action)
                end = self._destination_point(state, action)
                self._set_phase(
                    "input_drag",
                    f"Karte ziehen: DOWN → HALTEN+BEWEGEN → UP ({action.notation()})",
                )
                result = ctl.held_mouse_drag(
                    start,
                    end,
                    duration_ms=900,
                    steps=52,
                )
                input_used = "held_mouse_drag"

            debug["attempts"].append({
                "mode": input_used,
                "result": result,
            })

            time.sleep(self.config.action_delay)
            after = ctl.capture()
            diff = frame_difference(before, after)
            return diff >= 0.0015, diff, after, input_used, debug

        except Exception as exc:
            try:
                after = ctl.capture()
                diff = frame_difference(before, after)
            except Exception:
                after = before
                diff = 0.0

            debug["attempts"].append({
                "mode": "held_mouse_drag",
                "error": f"{type(exc).__name__}: {exc}",
            })
            return False, diff, after, "held_mouse_drag_failed", debug

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
        raw_actions = list(actions)
        banned = self.failed_actions.get(sig, set())
        actions = [a for a in actions if a.notation() not in banned]

        # OCR/UIA state signatures can remain identical while input attempts fail.
        # Do not permanently exhaust every legal move and then stop. Once every
        # currently legal tableau move has been tried, clear only this state's
        # temporary input blacklist and let the model choose again.
        if not actions and raw_actions:
            self.failed_actions.pop(sig, None)
            actions = raw_actions
            banned = set()
            state.diagnostics.append(
                "Alle temporär gesperrten Züge dieser Stellung wurden wieder freigegeben."
            )

        if self.config.learning:
            self.learning.enrich_actions(
                self.config.model,
                sig,
                state,
                actions,
            )

        # Search first, model second: do not present dozens of weak legal moves
        # to Laya/Jev. Restrict the chooser to the strongest plausible options.
        if len(actions) > 8:
            actions = sorted(
                actions,
                key=lambda a: (a.total_score, a.lookahead_score, a.immediate_score),
                reverse=True,
            )[:8]
            state.diagnostics.append(
                "Strategie-Filter: nur die 8 besten legalen Kandidaten an das Modell gegeben."
            )

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

        # Deterministic strategy guard. Laya/Jev is a chooser, not a Spider
        # engine, so it may occasionally prefer a legal but obviously wasteful
        # option. The rule/search layer vetoes only clearly inferior choices.
        ranked = sorted(
            actions,
            key=lambda a: (a.total_score, a.lookahead_score, a.immediate_score),
            reverse=True,
        )
        best = ranked[0]
        original_selected = selected
        override_reason = None

        nonempty = [
            a for a in ranked
            if a.kind == "move" and not a.features.get("empty_destination", 0.0)
        ]
        if (
            selected.kind == "move"
            and selected.features.get("empty_destination", 0.0)
            and not selected.features.get("reveal_hidden", 0.0)
            and nonempty
        ):
            selected = nonempty[0]
            override_reason = "leeres Feld ohne Aufdecken vermieden"
        elif (
            selected.kind == "deal"
            and any(a.kind == "move" and a.total_score >= 0 for a in ranked)
        ):
            selected = next(a for a in ranked if a.kind == "move" and a.total_score >= 0)
            override_reason = "unnötiges Nachziehen vermieden"
        elif selected.total_score < best.total_score - 24.0:
            selected = best
            override_reason = (
                f"Modellwahl deutlich schlechter als Suchheuristik "
                f"({original_selected.total_score:.1f} vs {best.total_score:.1f})"
            )

        if override_reason:
            model_debug["strategy_guard"] = {
                "overridden": True,
                "reason": override_reason,
                "model_selected": original_selected.notation(),
                "executed": selected.notation(),
                "model_score": round(original_selected.total_score, 3),
                "executed_score": round(selected.total_score, 3),
            }
        else:
            model_debug["strategy_guard"] = {
                "overridden": False,
                "executed": selected.notation(),
            }

        if input_abort_requested():
            raise SpiderAgentError("STOP_REQUESTED")

        self._set_phase("input", f"Maus-Drag {selected.notation()}")
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
            next_state = self._verify_frame_state(after)
            next_sig = state_signature(next_state)
            board_changed = next_sig != sig
        except Exception as exc:
            next_state = None
            next_sig = sig
            board_changed = False
            input_debug["verify_error"] = f"{type(exc).__name__}: {exc}"

        accepted = bool(changed_pixels and board_changed)

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
                "Drag wurde gesendet, aber das erkannte Brett hat sich nicht geändert",
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
                    "Es wurde ausschließlich der verlangte Maus-Drag ausgeführt: "
                    "linke Taste DOWN, während der gesamten Bewegung gehalten, "
                    "am Ziel UP. Keine UIA-Auswahl und keine Tastaturnavigation."
                ),
            }
            return self.status()

        self.moves += 1
        won_now = bool(next_state is not None and self._is_win(next_state))
        learning_debug = None

        if self.config.learning:
            self.learning.record_move(self.config.model)

            repeated_state = bool(
                next_sig == sig
                or any(item.get("next_state_sig") == next_sig for item in self.trajectory[-20:])
            )
            if next_state is not None:
                reward = self.learning.reward_for_transition(
                    state,
                    selected,
                    next_state,
                    won=won_now,
                    repeated_state=repeated_state,
                )
                learning_debug = self.learning.observe_transition(
                    self.config.model,
                    state,
                    selected,
                    next_state,
                    reward=reward,
                    done=won_now,
                )

            self.trajectory.append(
                {
                    "state_sig": sig,
                    "next_state_sig": next_sig,
                    "action": selected.notation(),
                    "features": selected.features,
                    "reward": None if learning_debug is None else learning_debug.get("reward"),
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
            "deep_learning": learning_debug,
        }

        if won_now:
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
        input_status = self.controller.input_status() if self.controller is not None else {
            "mode": "not_connected",
            "physical_mouse_touched": False,
            "physical_mouse_scope": "none",
            "foreground_changed": False,
            "target": None,
        }
        return {
            "config": {
                "hwnd": self.config.hwnd,
                "model": self.config.model,
                "depth": self.config.depth,
                "stock_x": round(self.config.stock_x, 4),
                "stock_y": round(self.config.stock_y, 4),
                "learning": self.config.learning,
                "action_delay": self.config.action_delay,
                "input_mode": input_status.get("mode"),
                "physical_mouse_touched": input_status.get("physical_mouse_touched", False),
                "physical_mouse_scope": input_status.get("physical_mouse_scope", "none"),
                "physical_keyboard_touched": False,
                "foreground_window_changed": input_status.get("foreground_changed", False),
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
            "input_status": input_status,
        }
