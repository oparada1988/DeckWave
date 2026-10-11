"""
DeckWave Launcher Action
========================
Configuration hub for audio visualizer settings and page launcher.
"""

import os
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gdk, GLib
from loguru import logger as log

from src.backend.PluginManager.ActionBase import ActionBase
from src.backend.DeckManagement.InputIdentifier import Input
import globals as gl

from ...backend.AudioService import AudioService

class DeckWaveLauncherAction(ActionBase):
    def __init__(self, action_id: str, action_name: str, deck_controller, page, plugin_base, state: int, input_ident):
        super().__init__(action_id, action_name, deck_controller, page, plugin_base, state, input_ident)
        self.audio_service = AudioService.get_instance()

        # UI widgets tracking
        self._source_combo: Adw.ComboRow | None = None
        self._source_model: Gtk.StringList | None = None
        self._source_ids: list[str] = ["default"]

        self._color_mode_row: Adw.ComboRow | None = None
        self._peak_color_row: Adw.ActionRow | None = None
        self._stop_rows: list[Adw.ActionRow] = []

    def on_ready(self):
        # Set icon
        icon_path = os.path.join(self.plugin_base.PATH, "assets", "action_launcher.png")
        if os.path.isfile(icon_path):
            self.set_media(media_path=icon_path)

        # Trigger auto-provisioning for connected decks
        if hasattr(self.plugin_base, "_auto_provision_pages"):
            self.plugin_base._auto_provision_pages()

        # Apply settings to AudioService and PluginBase
        settings = self.get_settings()
        if hasattr(self.plugin_base, "set_global_settings") and settings:
            self.plugin_base.set_global_settings(settings)

        self.audio_service.set_settings(
            target_source=settings.get("audio_source", "default"),
            gain=float(settings.get("gain", 1.4)),
            decay_speed=settings.get("decay_speed", "normal")
        )

    def on_key_down(self):
        """Navigate to the visualizer page for this deck."""
        controller = self.deck_controller
        if not controller:
            return

        settings = self.get_settings()
        if hasattr(self.plugin_base, "set_global_settings") and settings:
            self.plugin_base.set_global_settings(settings)

        self.audio_service.set_settings(
            target_source=settings.get("audio_source", "default"),
            gain=float(settings.get("gain", 1.4)),
            decay_speed=settings.get("decay_speed", "normal")
        )

        # Determine deck model
        key_count = 15
        deck_type = ""
        if hasattr(controller, "deck"):
            deck_type = getattr(controller.deck, "deck_type", lambda: "")()
            if hasattr(controller.deck, "key_count"):
                try:
                    key_count = controller.deck.key_count()
                except Exception:
                    pass

        if "XL" in deck_type or key_count >= 32:
            page_filename = "DeckWave_XL.json"
        elif "Plus" in deck_type or key_count == 8 or hasattr(controller.deck, "touchscreen_image_format"):
            page_filename = "DeckWave_Plus.json"
        else:
            page_filename = "DeckWave_MK2.json"

        user_page_path = os.path.join(gl.DATA_PATH, "pages", page_filename)
        if not os.path.isfile(user_page_path):
            # Fallback to bundled
            user_page_path = os.path.join(self.plugin_base.PATH, "pages", page_filename)

        if os.path.isfile(user_page_path):
            # Save origin page
            origin = getattr(self, "page", None) or getattr(controller, "active_page", None)
            if origin:
                self.plugin_base.set_origin_page(id(controller), origin)

            # Start audio service
            self.audio_service.start()

            # Load page
            page_obj = gl.page_manager.get_page(user_page_path, deck_controller=controller)
            if page_obj:
                controller.load_page(page_obj)

    def get_custom_config_area(self) -> Adw.PreferencesGroup:
        """Builds the comprehensive configuration UI."""
        group = Adw.PreferencesGroup()
        group.set_title("DeckWave Visualizer Settings")

        settings = self.get_settings()

        # --- 1. Audio Settings ---
        audio_group = Adw.PreferencesGroup()
        audio_group.set_title("Audio Configuration")

        # Source ComboRow
        self._source_combo = Adw.ComboRow()
        self._source_combo.set_title("Audio Source")
        self._source_combo.set_subtitle("Select default system output or an active application")
        self._refresh_sources()
        self._source_combo.connect("notify::selected", self._on_source_changed)

        # Refresh Sources Button
        refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh_btn.set_valign(Gtk.Align.CENTER)
        refresh_btn.set_tooltip_text("Refresh active audio streams")
        refresh_btn.connect("clicked", lambda b: self._refresh_sources(b))
        self._source_combo.add_suffix(refresh_btn)
        audio_group.add(self._source_combo)

        # Gain Slider
        gain_val = float(settings.get("gain", 1.2))
        gain_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.5, 3.0, 0.1)
        gain_scale.set_value(gain_val)
        gain_scale.set_draw_value(True)
        gain_scale.set_value_pos(Gtk.PositionType.RIGHT)
        gain_scale.set_hexpand(True)
        gain_scale.connect("value-changed", self._on_gain_changed)

        gain_row = Adw.ActionRow()
        gain_row.set_title("Sensitivity Gain")
        gain_row.set_subtitle("Audio multiplier (0.5x to 3.0x)")
        gain_row.add_suffix(gain_scale)
        audio_group.add(gain_row)

        # Decay Speed
        decay_row = Adw.ComboRow()
        decay_row.set_title("Decay Speed")
        decay_row.set_subtitle("Visualizer falloff animation speed")
        decay_model = Gtk.StringList.new(["Fast", "Normal", "Slow"])
        decay_row.set_model(decay_model)
        speeds = ["fast", "normal", "slow"]
        curr_speed = settings.get("decay_speed", "normal")
        decay_row.set_selected(speeds.index(curr_speed) if curr_speed in speeds else 1)
        decay_row.connect("notify::selected", self._on_decay_changed)
        audio_group.add(decay_row)

        group.add(audio_group)

        # --- 2. Style Settings ---
        style_group = Adw.PreferencesGroup()
        style_group.set_title("Visualizer Appearance")

        # Bar Style
        style_row = Adw.ComboRow()
        style_row.set_title("Bar Style")
        style_row.set_subtitle("Smooth continuous fill, segmented LEDs, or peak hold caps")
        style_model = Gtk.StringList.new(["Solid Bar", "Stepped LEDs", "Peak Hold"])
        style_row.set_model(style_model)
        style_keys = ["solid", "stepped", "peak"]
        curr_style = settings.get("bar_style", "solid")
        style_row.set_selected(style_keys.index(curr_style) if curr_style in style_keys else 0)
        style_row.connect("notify::selected", self._on_style_changed)
        style_group.add(style_row)

        # Bar Background Color Picker
        bg_hex = settings.get("bg_color", "#121216")
        bg_row = self._create_color_row(
            title="Bar Background Color",
            current_hex=bg_hex,
            on_changed_cb=lambda hex_val: self._on_color_setting_changed("bg_color", hex_val),
            dialog_title="Select Background Color"
        )
        style_group.add(bg_row)

        # Peak Hold Indicator Toggle Switch
        show_peaks = settings.get("show_peaks", True)
        peak_switch = Gtk.Switch()
        peak_switch.set_active(show_peaks)
        peak_switch.set_valign(Gtk.Align.CENTER)
        peak_switch.connect("state-set", self._on_peaks_toggle)

        peaks_row = Adw.ActionRow()
        peaks_row.set_title("Peak Hold Indicator")
        peaks_row.set_subtitle("Display floating peak caps at the top of the audio bars")
        peaks_row.add_suffix(peak_switch)
        style_group.add(peaks_row)

        # Peak Cap Color Picker
        peak_hex = settings.get("peak_color", "#FFFFFF")
        self._peak_color_row = self._create_color_row(
            title="Peak Indicator Color",
            current_hex=peak_hex,
            on_changed_cb=lambda hex_val: self._on_color_setting_changed("peak_color", hex_val),
            dialog_title="Select Peak Indicator Color"
        )
        self._peak_color_row.set_visible(show_peaks)
        style_group.add(self._peak_color_row)

        group.add(style_group)

        # --- 3. Gradient Engine Settings ---
        grad_group = Adw.PreferencesGroup()
        grad_group.set_title("Gradient &amp; Color Palette")

        self._color_mode_row = Adw.ComboRow()
        self._color_mode_row.set_title("Color Mode")
        self._color_mode_row.set_subtitle("Single color or multi-stop gradient (up to 5 stops)")
        mode_model = Gtk.StringList.new([
            "1-Color (Solid)",
            "2-Color Gradient",
            "3-Color Gradient",
            "4-Color Gradient",
            "5-Color Gradient"
        ])
        self._color_mode_row.set_model(mode_model)
        modes = ["1-color", "2-color", "3-color", "4-color", "5-color"]
        curr_mode = settings.get("color_mode", "3-color")
        self._color_mode_row.set_selected(modes.index(curr_mode) if curr_mode in modes else 2)
        self._color_mode_row.connect("notify::selected", self._on_color_mode_changed)
        grad_group.add(self._color_mode_row)

        # Default stops: Neon/Cyberpunk palette
        default_stops = ["#00FF88", "#00D4FF", "#9D00FF", "#FF0088", "#FF3300"]
        stops = settings.get("color_stops", default_stops)
        while len(stops) < 5:
            stops.append(default_stops[len(stops)])

        self._stop_rows = []
        stop_labels = [
            ("Stop 1 (Base / Low)", "Select Base Color"),
            ("Stop 2 (Low-Mid)", "Select Low-Mid Color"),
            ("Stop 3 (Mid)", "Select Mid Color"),
            ("Stop 4 (High-Mid)", "Select High-Mid Color"),
            ("Stop 5 (Peak / High)", "Select Peak Color")
        ]

        for i, (title, dlg_title) in enumerate(stop_labels):
            s_row = self._create_color_row(
                title=title,
                current_hex=stops[i],
                on_changed_cb=self._make_stop_color_changed_handler(i),
                dialog_title=dlg_title
            )
            grad_group.add(s_row)
            self._stop_rows.append(s_row)

        self._update_stop_visibility(curr_mode)
        group.add(grad_group)

        # --- 4. Page Setup Button ---
        page_group = Adw.PreferencesGroup()
        page_group.set_title("Hardware Page Setup")

        prov_btn = Gtk.Button(label="Provision Pages")
        prov_btn.set_valign(Gtk.Align.CENTER)
        prov_btn.connect("clicked", lambda b: self._on_provision_clicked(b))

        prov_row = Adw.ActionRow()
        prov_row.set_title("Provision Visualizer Pages")
        prov_row.set_subtitle("Automatically create dedicated pages for connected Stream Decks")
        prov_row.add_suffix(prov_btn)
        page_group.add(prov_row)

        group.add(page_group)

        return group

    def _refresh_sources(self, button: Gtk.Button | None = None):
        """Populates the Audio Source dropdown with default, apps, and monitor sources."""
        if button:
            button.set_sensitive(False)
            button.set_icon_name("process-working-symbolic")

        try:
            targets = self.audio_service.get_active_audio_apps()
            names = ["Default System Audio"]
            self._source_ids = ["default"]

            for item in targets:
                names.append(item["name"])
                self._source_ids.append(item["id"])

            self._source_model = Gtk.StringList.new(names)
            if self._source_combo:
                self._source_combo.set_model(self._source_model)
                curr = self.get_settings().get("audio_source", "default")
                idx = self._source_ids.index(curr) if curr in self._source_ids else 0
                self._source_combo.set_selected(idx)
        finally:
            if button:
                def _restore():
                    button.set_icon_name("view-refresh-symbolic")
                    button.set_sensitive(True)
                    return False
                GLib.timeout_add(300, _restore)

    def _on_source_changed(self, combo, param):
        idx = combo.get_selected()
        if 0 <= idx < len(self._source_ids):
            src_id = self._source_ids[idx]
            settings = self.get_settings()
            settings["audio_source"] = src_id
            self.set_settings(settings)
            if hasattr(self.plugin_base, "set_global_settings"):
                self.plugin_base.set_global_settings(settings)
            self.audio_service.set_settings(target_source=src_id)

    def _on_gain_changed(self, scale):
        val = round(scale.get_value(), 2)
        settings = self.get_settings()
        settings["gain"] = val
        self.set_settings(settings)
        if hasattr(self.plugin_base, "set_global_settings"):
            self.plugin_base.set_global_settings(settings)
        self.audio_service.set_settings(gain=val)

    def _on_decay_changed(self, combo, param):
        speeds = ["fast", "normal", "slow"]
        idx = combo.get_selected()
        if 0 <= idx < len(speeds):
            val = speeds[idx]
            settings = self.get_settings()
            settings["decay_speed"] = val
            self.set_settings(settings)
            if hasattr(self.plugin_base, "set_global_settings"):
                self.plugin_base.set_global_settings(settings)
            self.audio_service.set_settings(decay_speed=val)

    def _on_style_changed(self, combo, param):
        styles = ["solid", "stepped", "peak"]
        idx = combo.get_selected()
        if 0 <= idx < len(styles):
            val = styles[idx]
            settings = self.get_settings()
            settings["bar_style"] = val
            self.set_settings(settings)
            if hasattr(self.plugin_base, "set_global_settings"):
                self.plugin_base.set_global_settings(settings)

    def _on_peaks_toggle(self, switch, state):
        settings = self.get_settings()
        val = bool(state)
        settings["show_peaks"] = val
        self.set_settings(settings)
        if hasattr(self.plugin_base, "set_global_settings"):
            self.plugin_base.set_global_settings(settings)
        if self._peak_color_row is not None:
            self._peak_color_row.set_visible(val)
        return False

    def _create_color_row(
        self,
        title: str,
        current_hex: str,
        on_changed_cb,
        dialog_title: str = "Select Color"
    ) -> Adw.ActionRow:
        row = Adw.ActionRow()
        row.set_title(title)
        row.set_subtitle(current_hex)

        rgba = Gdk.RGBA()
        if not rgba.parse(current_hex):
            rgba.parse("#FFFFFF")

        if hasattr(Gtk, "ColorDialogButton"):
            dialog = Gtk.ColorDialog(title=dialog_title)
            dialog.set_with_alpha(False)
            color_btn = Gtk.ColorDialogButton(dialog=dialog)
            color_btn.set_valign(Gtk.Align.CENTER)
            color_btn.set_rgba(rgba)

            def _on_color_set(btn, pspec):
                new_rgba = btn.get_rgba()
                hex_val = f"#{int(round(new_rgba.red * 255)):02X}{int(round(new_rgba.green * 255)):02X}{int(round(new_rgba.blue * 255)):02X}"
                row.set_subtitle(hex_val)
                on_changed_cb(hex_val)

            color_btn.connect("notify::rgba", _on_color_set)
        else:
            color_btn = Gtk.ColorButton()
            color_btn.set_valign(Gtk.Align.CENTER)
            color_btn.set_use_alpha(False)
            color_btn.set_rgba(rgba)

            def _on_color_chosen(btn):
                new_rgba = btn.get_rgba()
                hex_val = f"#{int(round(new_rgba.red * 255)):02X}{int(round(new_rgba.green * 255)):02X}{int(round(new_rgba.blue * 255)):02X}"
                row.set_subtitle(hex_val)
                on_changed_cb(hex_val)

            color_btn.connect("color-set", _on_color_chosen)

        row.add_suffix(color_btn)
        return row

    def _on_color_setting_changed(self, key: str, hex_val: str):
        settings = self.get_settings()
        settings[key] = hex_val.upper()
        self.set_settings(settings)
        if hasattr(self.plugin_base, "set_global_settings"):
            self.plugin_base.set_global_settings(settings)

    def _on_color_mode_changed(self, combo, param):
        modes = ["1-color", "2-color", "3-color", "4-color", "5-color"]
        idx = combo.get_selected()
        if 0 <= idx < len(modes):
            val = modes[idx]
            settings = self.get_settings()
            settings["color_mode"] = val
            self.set_settings(settings)
            if hasattr(self.plugin_base, "set_global_settings"):
                self.plugin_base.set_global_settings(settings)
            self._update_stop_visibility(val)

    def _update_stop_visibility(self, mode: str):
        count_map = {"1-color": 1, "2-color": 2, "3-color": 3, "4-color": 4, "5-color": 5}
        visible_count = count_map.get(mode, 3)
        for i, row in enumerate(self._stop_rows):
            row.set_visible(i < visible_count)

    def _make_stop_color_changed_handler(self, index: int):
        def _handler(hex_val: str):
            settings = self.get_settings()
            stops = settings.get("color_stops", ["#00FF88", "#00D4FF", "#9D00FF", "#FF0088", "#FF3300"])
            while len(stops) <= index:
                stops.append("#FFFFFF")
            stops[index] = hex_val.upper()
            settings["color_stops"] = stops
            self.set_settings(settings)
            if hasattr(self.plugin_base, "set_global_settings"):
                self.plugin_base.set_global_settings(settings)
        return _handler

    def _on_provision_clicked(self, button):
        if hasattr(self.plugin_base, "_auto_provision_pages"):
            self.plugin_base._auto_provision_pages()
            button.set_label("Provisioned!")
            GLib.timeout_add(1500, lambda: button.set_label("Provision Pages") or False)
