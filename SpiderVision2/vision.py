from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
RANK_VALUE = {rank: i + 1 for i, rank in enumerate(RANKS)}


@dataclass
class CardObservation:
    rank: str
    confidence: float
    x: float
    y: float
    patch_box: tuple[int, int, int, int]
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "confidence": round(float(self.confidence), 3),
            "x": round(float(self.x), 1),
            "y": round(float(self.y), 1),
            "patch_box": list(self.patch_box),
            "source": self.source,
        }


@dataclass
class ColumnObservation:
    index: int
    x: float
    cards: list[CardObservation] = field(default_factory=list)
    hidden_above: bool = False
    empty_confident: bool = False
    status: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "x": round(float(self.x), 1),
            "cards": [c.to_dict() for c in self.cards],
            "hidden_above": self.hidden_above,
            "empty_confident": self.empty_confident,
            "status": self.status,
        }


@dataclass
class BoardObservation:
    width: int
    height: int
    columns: list[ColumnObservation]
    complete: bool
    uncertain_cards: int
    diagnostics: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "complete": self.complete,
            "uncertain_cards": self.uncertain_cards,
            "columns": [c.to_dict() for c in self.columns],
            "diagnostics": self.diagnostics,
        }


class TemplateBank:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or Path(__file__).with_name("data") / "templates")
        self.root.mkdir(parents=True, exist_ok=True)
        self.learned: dict[str, list[np.ndarray]] = {rank: [] for rank in RANKS}
        self.synthetic: dict[str, list[np.ndarray]] = {rank: [] for rank in RANKS}
        self.reload()

    @staticmethod
    def normalize_patch(patch: np.ndarray) -> np.ndarray:
        if patch.ndim == 3:
            gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        else:
            gray = patch.copy()

        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        # Dark ink on a light card -> white foreground.
        _, bw = cv2.threshold(gray, 145, 255, cv2.THRESH_BINARY_INV)

        # Remove isolated texture noise.
        kernel = np.ones((2, 2), np.uint8)
        bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kernel)

        pts = cv2.findNonZero(bw)
        if pts is None:
            return np.zeros((64, 64), dtype=np.uint8)

        x, y, w, h = cv2.boundingRect(pts)
        crop = bw[y:y + h, x:x + w]
        if crop.size == 0:
            return np.zeros((64, 64), dtype=np.uint8)

        target = np.zeros((64, 64), dtype=np.uint8)
        scale = min(52.0 / max(1, w), 52.0 / max(1, h))
        nw = max(1, int(round(w * scale)))
        nh = max(1, int(round(h * scale)))
        resized = cv2.resize(crop, (nw, nh), interpolation=cv2.INTER_AREA)
        ox = (64 - nw) // 2
        oy = (64 - nh) // 2
        target[oy:oy + nh, ox:ox + nw] = resized
        return target

    def _font_candidates(self) -> list[str]:
        roots = [
            r"C:\Windows\Fonts\arialbd.ttf",
            r"C:\Windows\Fonts\segoeuib.ttf",
            r"C:\Windows\Fonts\calibrib.ttf",
            r"C:\Windows\Fonts\tahomabd.ttf",
            r"C:\Windows\Fonts\georgiab.ttf",
            r"C:\Windows\Fonts\timesbd.ttf",
        ]
        return [p for p in roots if os.path.exists(p)]

    def _build_synthetic(self) -> None:
        fonts = self._font_candidates()
        if not fonts:
            return

        for rank in RANKS:
            bucket: list[np.ndarray] = []
            for font_path in fonts:
                for size in (26, 30, 34, 38):
                    try:
                        font = ImageFont.truetype(font_path, size)
                    except Exception:
                        continue
                    canvas = Image.new("L", (96, 96), 255)
                    draw = ImageDraw.Draw(canvas)
                    draw.text((6, 1), rank, fill=0, font=font)
                    # One-suit Spider: the corner symbol is always a spade.
                    try:
                        symfont = ImageFont.truetype(font_path, max(18, int(size * 0.65)))
                        draw.text((9, size + 1), "♠", fill=0, font=symfont)
                    except Exception:
                        pass
                    arr = np.asarray(canvas)
                    bucket.append(self.normalize_patch(arr))
            self.synthetic[rank] = bucket

    def reload(self) -> None:
        self.learned = {rank: [] for rank in RANKS}
        for rank in RANKS:
            folder = self.root / rank
            folder.mkdir(parents=True, exist_ok=True)
            for path in sorted(folder.glob("*.png")):
                img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
                if img is not None:
                    self.learned[rank].append(self.normalize_patch(img))
        self._build_synthetic()

    @staticmethod
    def _similarity(a: np.ndarray, b: np.ndarray) -> float:
        af = a.astype(np.float32) / 255.0
        bf = b.astype(np.float32) / 255.0
        # Combination of correlation and foreground IoU.
        corr = float(cv2.matchTemplate(af, bf, cv2.TM_CCOEFF_NORMED)[0, 0])
        aa = af > 0.35
        bb = bf > 0.35
        inter = float(np.logical_and(aa, bb).sum())
        union = float(np.logical_or(aa, bb).sum())
        iou = inter / union if union else 0.0
        return 0.72 * max(-1.0, corr) + 0.28 * iou

    def classify(self, patch: np.ndarray) -> tuple[str, float, dict[str, float], str]:
        obs = self.normalize_patch(patch)
        scores: dict[str, float] = {}
        sources: dict[str, str] = {}

        for rank in RANKS:
            best = -1.0
            source = "none"
            for tmpl in self.learned.get(rank, []):
                score = self._similarity(obs, tmpl) + 0.06
                if score > best:
                    best = score
                    source = "learned"
            for tmpl in self.synthetic.get(rank, []):
                score = self._similarity(obs, tmpl)
                if score > best:
                    best = score
                    source = "synthetic"
            scores[rank] = float(best)
            sources[rank] = source

        ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        best_rank, best_score = ordered[0]
        second = ordered[1][1] if len(ordered) > 1 else -1.0
        margin = best_score - second

        # Learned samples can pass a little lower because they match the actual
        # Microsoft Solitaire theme rather than a synthetic font.
        source = sources.get(best_rank, "none")
        min_score = 0.48 if source == "learned" else 0.56
        min_margin = 0.025 if source == "learned" else 0.045

        if best_score < min_score or margin < min_margin:
            return "?", float(best_score), scores, source
        return best_rank, float(best_score), scores, source

    def learn(self, rank: str, patch: np.ndarray) -> str:
        rank = rank.upper().strip()
        if rank not in RANKS:
            raise ValueError(f"Ungültiger Rang: {rank}")
        normalized = self.normalize_patch(patch)
        folder = self.root / rank
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{int(time.time() * 1000)}.png"
        path = folder / name
        cv2.imwrite(str(path), normalized)
        self.reload()
        return str(path)

    def summary(self) -> dict[str, int]:
        return {rank: len(self.learned.get(rank, [])) for rank in RANKS}


class SpiderVision:
    def __init__(self, template_root: str | Path | None = None):
        self.templates = TemplateBank(template_root)
        self.last_patches: dict[tuple[int, int], np.ndarray] = {}

    @staticmethod
    def column_centers(width: int) -> list[float]:
        # Normalized geometry of Microsoft Spider. The detector does not ask
        # UI Automation for card positions.
        return [width * (0.105 + 0.088 * i) for i in range(10)]

    @staticmethod
    def _purple_mask(frame: np.ndarray) -> np.ndarray:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
        return ((H >= 130) & (H <= 179) & (S >= 55) & (V >= 45)).astype(np.uint8)

    @staticmethod
    def _light_mask(frame: np.ndarray) -> np.ndarray:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        S, V = hsv[:, :, 1], hsv[:, :, 2]
        return ((V >= 135) & (S <= 125)).astype(np.uint8)

    def _candidate_tops(self, frame: np.ndarray, cx: float) -> list[int]:
        h, w = frame.shape[:2]
        x0 = max(0, int(cx - w * 0.036))
        x1 = min(w, int(cx + w * 0.036))
        y0 = int(h * 0.115)
        y1 = int(h * 0.72)

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        light = self._light_mask(frame)

        roi_gray = gray[y0:y1, x0:x1]
        roi_light = light[y0:y1, x0:x1]
        if roi_gray.size == 0:
            return []

        # Strong horizontal borders of exposed white cards.
        sobel = cv2.Sobel(roi_gray, cv2.CV_32F, 0, 1, ksize=3)
        edge = np.mean(np.abs(sobel), axis=1)
        edge = cv2.GaussianBlur(edge.reshape(-1, 1), (1, 9), 0).reshape(-1)

        light_ratio = np.mean(roi_light, axis=1)
        light_smooth = cv2.GaussianBlur(light_ratio.reshape(-1, 1).astype(np.float32), (1, 7), 0).reshape(-1)

        candidates: list[tuple[float, int]] = []
        min_sep = max(12, int(h * 0.020))
        for yy in range(4, len(edge) - 8):
            below = float(np.mean(light_smooth[yy:min(len(light_smooth), yy + max(6, int(h * 0.012)))]))
            if below < 0.28:
                continue
            if edge[yy] < 18.0:
                continue
            if edge[yy] < edge[yy - 2] or edge[yy] < edge[yy + 2]:
                continue
            score = float(edge[yy]) + below * 30.0
            candidates.append((score, y0 + yy))

        # Non-maximum suppression in Y.
        candidates.sort(reverse=True)
        chosen: list[int] = []
        for _score, y in candidates:
            if all(abs(y - prev) >= min_sep for prev in chosen):
                chosen.append(y)
            if len(chosen) >= 14:
                break

        chosen.sort()

        # Reject card-like edges that are too low in the play field unless they
        # really have a light card body below them.
        out: list[int] = []
        for y in chosen:
            yy = y - y0
            below = float(np.mean(light_smooth[yy:min(len(light_smooth), yy + max(10, int(h * 0.03)))]))
            if below >= 0.24:
                out.append(y)
        return out

    def _rank_patch(self, frame: np.ndarray, cx: float, top_y: int) -> tuple[np.ndarray, tuple[int, int, int, int]]:
        h, w = frame.shape[:2]
        card_w = w * 0.074
        left = cx - card_w / 2.0

        x0 = max(0, int(round(left + max(4, card_w * 0.045))))
        x1 = min(w, int(round(left + card_w * 0.34)))
        y0 = max(0, int(round(top_y + max(3, h * 0.004))))
        y1 = min(h, int(round(top_y + max(32, h * 0.052))))

        patch = frame[y0:y1, x0:x1].copy()
        return patch, (x0, y0, x1, y1)

    def detect(self, frame: np.ndarray) -> tuple[BoardObservation, np.ndarray]:
        self.last_patches = {}
        h, w = frame.shape[:2]
        centers = self.column_centers(w)
        purple = self._purple_mask(frame)
        light = self._light_mask(frame)
        columns: list[ColumnObservation] = []
        diagnostics: list[str] = []
        uncertain = 0

        for i, cx in enumerate(centers):
            tops = self._candidate_tops(frame, cx)
            col = ColumnObservation(index=i, x=cx)

            # Purple area above the first visible card indicates hidden cards.
            x0 = max(0, int(cx - w * 0.028))
            x1 = min(w, int(cx + w * 0.028))
            scan_y0 = int(h * 0.11)
            scan_y1 = int(h * 0.60)
            p_roi = purple[scan_y0:scan_y1, x0:x1]
            purple_ratio = float(p_roi.mean()) if p_roi.size else 0.0

            # Keep only candidates whose rank corner contains enough dark ink.
            accepted_tops: list[int] = []
            for top in tops:
                patch, _box = self._rank_patch(frame, cx, top)
                if patch.size == 0:
                    continue
                gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
                dark_ratio = float((gray < 115).mean())
                light_ratio = float((gray > 150).mean())
                if dark_ratio >= 0.018 and light_ratio >= 0.30:
                    accepted_tops.append(top)

            # A single large card can produce more than one horizontal edge.
            # Remove near-duplicates while keeping the upper edge.
            cleaned: list[int] = []
            for top in accepted_tops:
                if not cleaned or top - cleaned[-1] >= max(14, int(h * 0.020)):
                    cleaned.append(top)

            for card_index, top in enumerate(cleaned):
                patch, box = self._rank_patch(frame, cx, top)
                rank, conf, _scores, source = self.templates.classify(patch)
                if rank == "?":
                    uncertain += 1
                card = CardObservation(
                    rank=rank,
                    confidence=conf,
                    x=cx,
                    y=float(top),
                    patch_box=box,
                    source=source,
                )
                col.cards.append(card)
                self.last_patches[(i, card_index)] = patch

            if col.cards:
                first_y = int(col.cards[0].y)
                hidden_roi = purple[scan_y0:max(scan_y0 + 1, first_y), x0:x1]
                hidden_ratio = float(hidden_roi.mean()) if hidden_roi.size else 0.0
                col.hidden_above = hidden_ratio >= 0.025
                col.status = "recognized" if all(c.rank != "?" for c in col.cards) else "uncertain"
            else:
                # Empty column is accepted only when neither purple backs nor
                # white card pixels are present in the tableau lane.
                l_roi = light[scan_y0:scan_y1, x0:x1]
                light_ratio = float(l_roi.mean()) if l_roi.size else 0.0
                col.empty_confident = purple_ratio < 0.010 and light_ratio < 0.035
                col.status = "empty" if col.empty_confident else "unknown"

            columns.append(col)

        unknown_cols = [c.index + 1 for c in columns if c.status == "unknown"]
        uncertain_cols = [c.index + 1 for c in columns if c.status == "uncertain"]
        complete = not unknown_cols and not uncertain_cols and sum(len(c.cards) for c in columns) > 0

        if unknown_cols:
            diagnostics.append(
                "Nicht sicher gelesen: Spalte(n) " + ", ".join(map(str, unknown_cols))
            )
        if uncertain_cols:
            diagnostics.append(
                "Rang unsicher: Spalte(n) " + ", ".join(map(str, uncertain_cols))
            )
        diagnostics.append(
            f"Visuell erkannte Spalten: {sum(c.status in ('recognized', 'empty') for c in columns)}/10"
        )
        diagnostics.append(
            f"Erkannte sichtbare Karten: {sum(len(c.cards) for c in columns)}"
        )
        diagnostics.append(
            "Kein UIA, kein OCR, kein LLM: nur Screenshot + OpenCV + gelernte Templates."
        )

        board = BoardObservation(
            width=w,
            height=h,
            columns=columns,
            complete=complete,
            uncertain_cards=uncertain,
            diagnostics=diagnostics,
        )
        annotated = self.annotate(frame, board)
        return board, annotated

    def annotate(self, frame: np.ndarray, board: BoardObservation) -> np.ndarray:
        out = frame.copy()
        h, w = out.shape[:2]

        for col in board.columns:
            x = int(round(col.x))
            cv2.line(out, (x, int(h * 0.10)), (x, int(h * 0.73)), (255, 180, 0), 1, cv2.LINE_AA)
            cv2.putText(
                out,
                f"C{col.index + 1}",
                (x - 14, int(h * 0.105)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.48,
                (255, 220, 80),
                1,
                cv2.LINE_AA,
            )

            for card in col.cards:
                x0, y0, x1, y1 = card.patch_box
                color = (60, 220, 60) if card.rank != "?" else (0, 165, 255)
                cv2.rectangle(out, (x0, y0), (x1, y1), color, 2)
                label = f"{card.rank} {card.confidence:.2f}"
                cv2.putText(
                    out,
                    label,
                    (x0, max(18, y0 - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.48,
                    color,
                    1,
                    cv2.LINE_AA,
                )

            if col.status == "unknown":
                cv2.putText(
                    out,
                    "UNKNOWN",
                    (x - 35, int(h * 0.76)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.42,
                    (0, 0, 255),
                    1,
                    cv2.LINE_AA,
                )
            elif col.status == "empty":
                cv2.putText(
                    out,
                    "EMPTY",
                    (x - 27, int(h * 0.76)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.42,
                    (160, 255, 160),
                    1,
                    cv2.LINE_AA,
                )

        banner = "BOARD OK" if board.complete else "BOARD NICHT SICHER - KEINE MAUSAKTION"
        banner_color = (60, 220, 60) if board.complete else (0, 80, 255)
        cv2.rectangle(out, (12, 12), (min(w - 12, 610), 52), (10, 10, 10), -1)
        cv2.putText(
            out,
            banner,
            (24, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.72,
            banner_color,
            2,
            cv2.LINE_AA,
        )
        return out

    def learn_last_patch(self, column: int, card_index: int, rank: str) -> str:
        key = (int(column), int(card_index))
        patch = self.last_patches.get(key)
        if patch is None:
            raise ValueError("Für diese Karte liegt kein aktueller Bildausschnitt vor.")
        return self.templates.learn(rank, patch)
