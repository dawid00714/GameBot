from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

RANK_VALUE = {
    "A": 1,
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
    "6": 6,
    "7": 7,
    "8": 8,
    "9": 9,
    "10": 10,
    "J": 11,
    "Q": 12,
    "K": 13,
}

_RANK_WORDS = {
    "A": ("a", "ace", "ass", "as"),
    "J": ("j", "jack", "bube"),
    "Q": ("q", "queen", "dame"),
    "K": ("k", "king", "könig", "koenig"),
}
_SUIT_WORDS = {
    "S": ("spade", "spades", "pik", "♠"),
    "H": ("heart", "hearts", "herz", "♥"),
    "D": ("diamond", "diamonds", "karo", "♦"),
    "C": ("club", "clubs", "kreuz", "♣"),
}


@dataclass
class VisibleCard:
    rank: str
    suit: str
    x: float
    y: float
    width: float
    height: float
    source: str = "uia"
    confidence: float = 1.0

    @property
    def value(self) -> int:
        return RANK_VALUE[self.rank]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "suit": self.suit,
            "x": round(self.x, 1),
            "y": round(self.y, 1),
            "width": round(self.width, 1),
            "height": round(self.height, 1),
            "source": self.source,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class ColumnState:
    index: int
    x: float
    cards: list[VisibleCard] = field(default_factory=list)
    hidden_above: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "x": round(self.x, 1),
            "hidden_above": self.hidden_above,
            "cards": [c.to_dict() for c in self.cards],
        }


@dataclass
class SpiderState:
    width: int
    height: int
    columns: list[ColumnState]
    stock_available: bool
    reader: str
    diagnostics: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "stock_available": self.stock_available,
            "reader": self.reader,
            "columns": [c.to_dict() for c in self.columns],
            "diagnostics": self.diagnostics[-20:],
        }


def _parse_rank(text: str) -> str | None:
    t = text.strip().lower()
    # Prefer explicit 10 before single digits.
    if re.search(r"(?<!\d)10(?!\d)", t):
        return "10"
    for n in range(2, 10):
        if re.search(rf"(?<!\d){n}(?!\d)", t):
            return str(n)
    words = re.findall(r"[a-zäöüß]+", t)
    tokens = set(words)
    for rank, variants in _RANK_WORDS.items():
        if any(v in tokens or re.search(rf"\b{re.escape(v)}\b", t) for v in variants):
            return rank
    # OCR often returns a naked face-card letter.
    stripped = re.sub(r"[^a-z0-9]", "", t)
    if stripped.upper() in ("A", "J", "Q", "K"):
        return stripped.upper()
    return None


def _parse_suit(text: str) -> str:
    t = text.lower()
    for suit, variants in _SUIT_WORDS.items():
        if any(v in t for v in variants):
            return suit
    # The user's game screenshot is one-suit Spider. If accessibility/OCR only
    # gives the rank, treat it as spades so the move generator can still work.
    return "S"


def _column_centers(width: int) -> list[float]:
    # Microsoft Spider layout seen in the supplied screenshot. These are client
    # ratios, not screen coordinates, so resizing keeps them aligned.
    return [width * (0.115 + i * 0.085) for i in range(10)]


def _group_cards(
    cards: list[VisibleCard],
    width: int,
    height: int,
    frame: np.ndarray | None = None,
) -> list[ColumnState]:
    centers = _column_centers(width)

    # If we have enough detected cards, derive centers from their x positions.
    xs = sorted(c.x for c in cards)
    if len(xs) >= 8:
        # Cluster x centers by nearest predefined column, then use cluster means.
        buckets: list[list[float]] = [[] for _ in range(10)]
        for x in xs:
            idx = min(range(10), key=lambda i: abs(centers[i] - x))
            if abs(centers[idx] - x) < width * 0.06:
                buckets[idx].append(x)
        for i, vals in enumerate(buckets):
            if vals:
                centers[i] = sum(vals) / len(vals)

    cols = [ColumnState(index=i, x=centers[i]) for i in range(10)]
    for card in cards:
        i = min(range(10), key=lambda j: abs(centers[j] - card.x))
        if abs(centers[i] - card.x) <= width * 0.065:
            cols[i].cards.append(card)

    for col in cols:
        col.cards.sort(key=lambda c: c.y)
        # Remove near-duplicate accessibility/OCR elements.
        dedup: list[VisibleCard] = []
        for c in col.cards:
            if dedup and c.rank == dedup[-1].rank and abs(c.y - dedup[-1].y) < max(5, height * 0.012):
                if c.confidence > dedup[-1].confidence:
                    dedup[-1] = c
            else:
                dedup.append(c)
        col.cards = dedup

    if frame is not None:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        for col in cols:
            x = int(min(width - 1, max(0, col.x)))
            # Face-down backs are magenta/purple in the supplied Windows theme.
            # We only need a boolean "something hidden is above the visible run".
            first_y = int(col.cards[0].y) if col.cards else int(height * 0.55)
            y0 = int(height * 0.10)
            y1 = max(y0 + 1, min(height, first_y))
            x0 = max(0, x - int(width * 0.025))
            x1 = min(width, x + int(width * 0.025))
            roi = hsv[y0:y1, x0:x1]
            if roi.size:
                h = roi[:, :, 0]
                s = roi[:, :, 1]
                v = roi[:, :, 2]
                purple = (((h >= 135) & (h <= 175) & (s > 55) & (v > 45))).mean()
                col.hidden_above = bool(purple > 0.025)

    return cols


def _read_uia(hwnd: int, width: int, height: int, client_left: int, client_top: int) -> tuple[list[VisibleCard], list[str]]:
    cards: list[VisibleCard] = []
    diag: list[str] = []
    try:
        from pywinauto import Desktop
        root = Desktop(backend="uia").window(handle=hwnd)
        descendants = root.descendants()
    except Exception as exc:
        return [], [f"UIA nicht verfügbar: {type(exc).__name__}: {exc}"]

    seen: set[tuple[str, int, int]] = set()
    for ctrl in descendants:
        try:
            name = (ctrl.element_info.name or "").strip()
            if not name:
                continue
            rank = _parse_rank(name)
            if rank is None:
                continue
            rect = ctrl.rectangle()
            cx = (rect.left + rect.right) / 2.0 - client_left
            cy = (rect.top + rect.bottom) / 2.0 - client_top
            w = max(1.0, rect.right - rect.left)
            h = max(1.0, rect.bottom - rect.top)
            if not (0 <= cx < width and height * 0.08 <= cy < height * 0.92):
                continue
            key = (rank, int(cx // 4), int(cy // 4))
            if key in seen:
                continue
            seen.add(key)
            cards.append(
                VisibleCard(
                    rank=rank,
                    suit=_parse_suit(name),
                    x=cx,
                    y=cy,
                    width=w,
                    height=h,
                    source="uia",
                    confidence=1.0,
                )
            )
        except Exception:
            continue

    diag.append(f"UIA: {len(cards)} Karten-/Rang-Elemente erkannt")
    return cards, diag


_ocr_engine = None


def _read_ocr(frame: np.ndarray) -> tuple[list[VisibleCard], list[str]]:
    global _ocr_engine
    cards: list[VisibleCard] = []
    diag: list[str] = []
    try:
        if _ocr_engine is None:
            from rapidocr_onnxruntime import RapidOCR
            _ocr_engine = RapidOCR()
        result, _elapsed = _ocr_engine(frame)
    except Exception as exc:
        return [], [f"OCR nicht verfügbar: {type(exc).__name__}: {exc}"]

    if not result:
        return [], ["OCR: keine Texte erkannt"]

    h, w = frame.shape[:2]
    for item in result:
        try:
            box, text, score = item
            rank = _parse_rank(str(text))
            if rank is None or float(score) < 0.30:
                continue
            pts = np.asarray(box, dtype=float)
            cx = float(pts[:, 0].mean())
            cy = float(pts[:, 1].mean())
            bw = float(pts[:, 0].max() - pts[:, 0].min())
            bh = float(pts[:, 1].max() - pts[:, 1].min())
            # Tableau zone only; this rejects score/time/menu digits.
            if not (w * 0.05 < cx < w * 0.95 and h * 0.12 < cy < h * 0.82):
                continue
            cards.append(
                VisibleCard(
                    rank=rank,
                    suit=_parse_suit(str(text)),
                    x=cx,
                    y=cy,
                    width=max(8.0, bw),
                    height=max(8.0, bh),
                    source="ocr",
                    confidence=float(score),
                )
            )
        except Exception:
            continue

    diag.append(f"OCR: {len(cards)} Rang-Glyphen im Tableau erkannt")
    return cards, diag


def _detect_stock(frame: np.ndarray, nx: float, ny: float) -> bool:
    h, w = frame.shape[:2]
    cx = int(max(0, min(w - 1, nx * w)))
    cy = int(max(0, min(h - 1, ny * h)))
    rw = max(12, int(w * 0.05))
    rh = max(12, int(h * 0.07))
    roi = frame[max(0, cy-rh):min(h, cy+rh), max(0, cx-rw):min(w, cx+rw)]
    if roi.size == 0:
        return False
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    purple = ((H >= 135) & (H <= 175) & (S > 45) & (V > 40)).mean()
    # Card back in the supplied theme is strongly purple/magenta.
    return bool(purple > 0.04)


def read_state(
    hwnd: int,
    frame: np.ndarray,
    client_left: int,
    client_top: int,
    stock_point: tuple[float, float] = (0.82, 0.78),
) -> SpiderState:
    height, width = frame.shape[:2]
    uia_cards, diag = _read_uia(hwnd, width, height, client_left, client_top)

    if len(uia_cards) >= 6:
        cards = uia_cards
        reader = "uia"
    else:
        ocr_cards, ocr_diag = _read_ocr(frame)
        diag.extend(ocr_diag)
        cards = ocr_cards if len(ocr_cards) > len(uia_cards) else uia_cards
        reader = "ocr" if cards is ocr_cards else "uia-partial"

    cols = _group_cards(cards, width, height, frame)
    stock = _detect_stock(frame, stock_point[0], stock_point[1])

    if sum(len(c.cards) for c in cols) < 5:
        diag.append(
            "Zu wenige Karten erkannt. Im Web-UI 'Diagnose' prüfen; "
            "das Spiel sollte sichtbar und nicht von Paint überdeckt sein."
        )

    return SpiderState(
        width=width,
        height=height,
        columns=cols,
        stock_available=stock,
        reader=reader,
        diagnostics=diag,
    )
