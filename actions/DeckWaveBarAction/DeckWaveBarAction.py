"""
DeckWave Bar Action
===================
Renders an individual audio visualizer column slice on a Stream Deck key.
Automatically determines its column (frequency band) and row (vertical slice)
based on its physical grid position.
"""

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib
from loguru import logger as log

from src.backend.PluginManager.ActionBase import ActionBase
from src.backend.DeckManagement.DeckController import ControllerKey
from src.backend.DeckManagement.InputIdentifier import Input

from ...backend.AudioService import AudioService
from ...backend.GradientEngine import GradientEngine

class DeckWaveBarAction(ActionBase):
    def __init__(self, action_id: str, action_name: str, deck_controller, page, plugin_base, state: int, input_ident):
        super().__init__(action_id, action_name, deck_controller, page, plugin_base, state, input_ident)
        self.audio_service = AudioService.get_instance()
        self.gradient_engine = GradientEngine()

        self._timer_id: int | None = None
        self._col: int = 0
        self._row: int = 0
        self._total_cols: int = 8
        self._total_rows: int = 4
        self._key_width: int = 96
        self._key_height: int = 96

        self._last_level: float = -1.0
        self._last_peak: float = -1.0

    def on_ready(self):
        self._calc_coordinates()
        self._start_render_loop()

    def on_remove(self):
        self._stop_render_loop()

    def _calc_coordinates(self):
        """Deduces column and row coordinates from hardware layout."""
        controller = self.deck_controller
        if not controller or not hasattr(controller, "deck"):
            return

        deck = controller.deck
        try:
            self._total_rows, self._total_cols = deck.key_layout()
            idx = self.input_ident.get_index(controller)
            self._col, self._row = ControllerKey.Index_To_Coords(deck, idx)
            img_format = deck.key_image_format()
            self._key_width, self._key_height = img_format["size"]
        except Exception as e:
            log.debug(f"DeckWaveBar coordinate deduction fallback: {e}")

    def _start_render_loop(self):
        if self._timer_id is None:
            # 33ms = ~30 FPS (silky smooth visualizer motion without overloading StreamDeck HID queue)
            self._timer_id = GLib.timeout_add(33, self._render_tick)

    def _stop_render_loop(self):
        if self._timer_id is not None:
            try:
                GLib.source_remove(self._timer_id)
            except Exception:
                pass
            self._timer_id = None

    def _render_tick(self) -> bool:
        if not self.get_is_present():
            self._timer_id = None
            return False

        # Query levels for our device's column count
        levels, peaks = self.audio_service.get_band_levels(self._total_cols)
        if self._col >= len(levels):
            return True

        level = levels[self._col]
        peak = peaks[self._col] if self._col < len(peaks) else 0.0

        # Dirty-check caching: skip redrawing if audio delta is imperceptible (< 0.5% change)
        if abs(level - self._last_level) < 0.005 and abs(peak - self._last_peak) < 0.005:
            return True

        self._last_level = level
        self._last_peak = peak

        # Retrieve global settings from launcher or defaults
        settings = self.plugin_base.get_global_settings()
        color_mode = settings.get("color_mode", "3-color")
        color_stops = settings.get("color_stops", ["#00FF88", "#00D4FF", "#9D00FF", "#FF0088", "#FF3300"])
        bg_color = settings.get("bg_color", "#121216")
        peak_color = settings.get("peak_color", "#FFFFFF")
        bar_style = settings.get("bar_style", "solid")

        # Render key image slice
        img = self.gradient_engine.render_key_cell(
            col=self._col,
            row=self._row,
            total_rows=self._total_rows,
            key_width=self._key_width,
            key_height=self._key_height,
            level=level,
            peak_level=peak,
            color_mode=color_mode,
            color_stops=color_stops,
            bg_color_hex=bg_color,
            peak_color_hex=peak_color,
            bar_style=bar_style
        )

        self.set_media(image=img, update=True)
        return True

    def on_key_down(self):
        """Tapping any visualizer bar returns to the previous page."""
        if hasattr(self.plugin_base, "return_to_origin"):
            self.plugin_base.return_to_origin(self.deck_controller)
