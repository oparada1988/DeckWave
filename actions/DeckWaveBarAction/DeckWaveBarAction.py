"""
DeckWave Bar Action
===================
Renders an individual audio visualizer column slice on a Stream Deck key.
Automatically determines its column (frequency band) and row (vertical slice)
based on its physical grid position.
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib
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
        self._showing_return: bool = False
        self._return_opacity: float = 1.0
        self._silent_frames: int = 0

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

        # Check return key settings for this specific key
        action_settings = self.get_settings()
        # Default: if coordinate is bottom-left (e.g. col 0, bottom row) or explicitly enabled
        is_bottom_left = (self._col == 0 and self._row == (self._total_rows - 1))
        is_return_enabled = action_settings.get("is_return_key", is_bottom_left)

        # Global deck activity detection: check if ANY band across the entire device is playing audio
        is_any_active = any(l >= 0.02 for l in levels) or any(p >= 0.02 for p in peaks)

        if is_return_enabled:
            settings = self.plugin_base.get_global_settings()
            bg_color = settings.get("bg_color", "#121216")
            arrow_color = "#FFFFFF"  # Return icon is always crisp white

            if is_any_active:
                # Active audio anywhere on deck -> fade OUT return icon immediately
                self._silent_frames = 0
                if self._return_opacity > 0.0:
                    self._return_opacity = max(0.0, self._return_opacity - 0.20)
                    return_img = self.gradient_engine.render_return_cell(
                        key_width=self._key_width,
                        key_height=self._key_height,
                        bg_color_hex=bg_color,
                        arrow_color_hex=arrow_color,
                        opacity=self._return_opacity
                    )
                    self.set_media(image=return_img, update=True)
                    self._last_level = -1.0  # Force visualizer redrawing immediately once opacity hits 0
                    return True
                self._showing_return = False
            else:
                # Silence across the whole deck -> wait brief debounce (~260ms), then fade IN return icon
                self._silent_frames += 1
                if self._silent_frames >= 8:
                    if self._return_opacity < 1.0:
                        self._return_opacity = min(1.0, self._return_opacity + 0.15)
                        return_img = self.gradient_engine.render_return_cell(
                            key_width=self._key_width,
                            key_height=self._key_height,
                            bg_color_hex=bg_color,
                            arrow_color_hex=arrow_color,
                            opacity=self._return_opacity
                        )
                        self.set_media(image=return_img, update=True)
                        return True
                    else:
                        if not self._showing_return:
                            self._showing_return = True
                            return_img = self.gradient_engine.render_return_cell(
                                key_width=self._key_width,
                                key_height=self._key_height,
                                bg_color_hex=bg_color,
                                arrow_color_hex=arrow_color,
                                opacity=1.0
                            )
                            self.set_media(image=return_img, update=True)
                        return True
        else:
            self._showing_return = False
            self._return_opacity = 0.0

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
        show_peaks = settings.get("show_peaks", True)

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
            bar_style=bar_style,
            show_peaks=show_peaks
        )

        self.set_media(image=img, update=True)
        return True

    def on_key_down(self):
        """Tapping any visualizer bar returns to the previous page."""
        if hasattr(self.plugin_base, "return_to_origin"):
            self.plugin_base.return_to_origin(self.deck_controller)

    def get_custom_config_area(self) -> Adw.PreferencesGroup:
        """Action settings: allows toggling smart return button on this key."""
        group = Adw.PreferencesGroup()
        group.set_title("DeckWave Bar Settings")

        settings = self.get_settings()
        is_bottom_left = (self._col == 0 and self._row == (self._total_rows - 1))
        is_return = settings.get("is_return_key", is_bottom_left)

        switch = Gtk.Switch()
        switch.set_active(is_return)
        switch.set_valign(Gtk.Align.CENTER)
        switch.connect("state-set", self._on_return_toggle)

        row = Adw.ActionRow()
        row.set_title("Smart Return Button")
        row.set_subtitle("Show return exit arrow when audio is silent, hide during audio playback")
        row.add_suffix(switch)
        group.add(row)

        return group

    def _on_return_toggle(self, switch, state):
        settings = self.get_settings()
        is_enabled = bool(state)
        settings["is_return_key"] = is_enabled
        self.set_settings(settings)
        # Reset state flag to force redraw
        self._last_level = -1.0
        self._showing_return = False
        self._return_opacity = 1.0 if is_enabled else 0.0
        self._silent_frames = 0
        return False
