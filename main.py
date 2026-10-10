"""
DeckWave - Real-Time Audio Visualizer Plugin for StreamController
=================================================================
Author: Oscar Parada
Repository: https://github.com/oparada1988/DeckWave
Description:
    Real-time audio visualizer wave bar plugin for Stream Deck MK2, XL, and Plus.
    Provides multi-stop color gradients, dynamic application audio stream targeting,
    and smart hardware page provisioning matching only connected devices.
"""

import os
import shutil
from loguru import logger as log

# Import StreamController base modules
from src.backend.PluginManager.PluginBase import PluginBase
from src.backend.PluginManager.ActionHolder import ActionHolder
from src.backend.PluginManager.ActionInputSupport import ActionInputSupport
from src.backend.DeckManagement.InputIdentifier import Input
import globals as gl

# Import actions
from .actions.DeckWaveLauncherAction.DeckWaveLauncherAction import DeckWaveLauncherAction
from .actions.DeckWaveBarAction.DeckWaveBarAction import DeckWaveBarAction
from .actions.DeckWaveReturnAction.DeckWaveReturnAction import DeckWaveReturnAction
from .backend.AudioService import AudioService

class DeckWavePlugin(PluginBase):
    def __init__(self):
        super().__init__()
        self.lm = self.locale_manager
        self.has_plugin_settings = True

        self.audio_service = AudioService.get_instance()
        self.origin_pages: dict[int, object] = {}

        # 1. Register Launcher Action
        self.launcher_holder = ActionHolder(
            plugin_base=self,
            action_base=DeckWaveLauncherAction,
            action_id="com_oparada1988_DeckWave::DeckWaveLauncherAction",
            action_name="DeckWave Launcher",
            action_support={
                Input.Key: ActionInputSupport.SUPPORTED,
                Input.Dial: ActionInputSupport.UNSUPPORTED,
                Input.Touchscreen: ActionInputSupport.UNSUPPORTED
            }
        )
        self.add_action_holder(self.launcher_holder)

        # 2. Register Visualizer Bar Action
        self.bar_holder = ActionHolder(
            plugin_base=self,
            action_base=DeckWaveBarAction,
            action_id="com_oparada1988_DeckWave::DeckWaveBarAction",
            action_name="DeckWave Bar",
            action_support={
                Input.Key: ActionInputSupport.SUPPORTED,
                Input.Dial: ActionInputSupport.UNSUPPORTED,
                Input.Touchscreen: ActionInputSupport.UNSUPPORTED
            }
        )
        self.add_action_holder(self.bar_holder)

        # 3. Register Return Action
        self.return_holder = ActionHolder(
            plugin_base=self,
            action_base=DeckWaveReturnAction,
            action_id="com_oparada1988_DeckWave::DeckWaveReturnAction",
            action_name="DeckWave Return",
            action_support={
                Input.Key: ActionInputSupport.SUPPORTED,
                Input.Dial: ActionInputSupport.UNSUPPORTED,
                Input.Touchscreen: ActionInputSupport.UNSUPPORTED
            }
        )
        self.add_action_holder(self.return_holder)

        # Register plugin metadata
        self.register(
            plugin_name="DeckWave",
            github_repo="https://github.com/oparada1988/DeckWave",
            plugin_version="1.0.0",
            app_version="1.5.0-beta"
        )

        # Auto-provision hardware-specific pages
        self._auto_provision_pages()

    def _auto_provision_pages(self):
        """
        Inspects connected physical Stream Deck hardware and selectively provisions
        visualizer pages ONLY for active models, eliminating unnecessary page clutter.
        """
        try:
            pages_dir = os.path.join(gl.DATA_PATH, "pages")
            os.makedirs(pages_dir, exist_ok=True)
            bundled_pages_dir = os.path.join(self.PATH, "pages")

            controllers = getattr(gl.deck_manager, "deck_controller", [])
            if not controllers:
                return

            has_xl = False
            has_plus = False
            has_mk2 = False

            for c in controllers:
                if not hasattr(c, "deck"):
                    continue
                deck = c.deck
                deck_type = getattr(deck, "deck_type", lambda: "")()
                key_count = getattr(deck, "key_count", lambda: 0)()

                if "XL" in deck_type or key_count >= 32:
                    has_xl = True
                elif "Plus" in deck_type or key_count == 8 or hasattr(deck, "touchscreen_image_format"):
                    has_plus = True
                elif "MK.2" in deck_type or "Original" in deck_type or key_count == 15:
                    has_mk2 = True

            # 1. Stream Deck XL
            dst_xl = os.path.join(pages_dir, "DeckWave_XL.json")
            src_xl = os.path.join(bundled_pages_dir, "DeckWave_XL.json")
            if has_xl and os.path.isfile(src_xl) and not os.path.exists(dst_xl):
                shutil.copy2(src_xl, dst_xl)
                log.info("DeckWave: Provisioned DeckWave_XL.json for connected Stream Deck XL.")

            # 2. Stream Deck Plus
            dst_plus = os.path.join(pages_dir, "DeckWave_Plus.json")
            src_plus = os.path.join(bundled_pages_dir, "DeckWave_Plus.json")
            if has_plus and os.path.isfile(src_plus) and not os.path.exists(dst_plus):
                shutil.copy2(src_plus, dst_plus)
                log.info("DeckWave: Provisioned DeckWave_Plus.json for connected Stream Deck +.")

            # 3. Stream Deck MK.2
            dst_mk2 = os.path.join(pages_dir, "DeckWave_MK2.json")
            src_mk2 = os.path.join(bundled_pages_dir, "DeckWave_MK2.json")
            if has_mk2 and os.path.isfile(src_mk2) and not os.path.exists(dst_mk2):
                shutil.copy2(src_mk2, dst_mk2)
                log.info("DeckWave: Provisioned DeckWave_MK2.json for connected Stream Deck MK.2.")

        except Exception as e:
            log.debug(f"DeckWave auto-provisioning exception: {e}")

    def set_origin_page(self, deck_id: int, page_obj):
        """Stores origin page for return navigation."""
        self.origin_pages[deck_id] = page_obj

    def return_to_origin(self, controller):
        """Navigates back to the previous page and stops audio capture when inactive."""
        if not controller:
            return

        origin = self.origin_pages.get(id(controller))
        if origin:
            if hasattr(origin, "get_name") and hasattr(origin, "json_path"):
                controller.load_page(origin)
                self._check_stop_audio()
                return
            elif isinstance(origin, str) and os.path.isfile(origin):
                page_obj = gl.page_manager.get_page(origin, deck_controller=controller)
                if page_obj:
                    controller.load_page(page_obj)
                    self._check_stop_audio()
                    return

        # Fallback to default page for this deck
        serial = ""
        if hasattr(controller, "deck") and hasattr(controller.deck, "get_serial_number"):
            try:
                serial = controller.deck.get_serial_number()
            except Exception:
                pass

        default_page = gl.page_manager.get_default_page(serial) if serial else None
        if default_page and os.path.isfile(default_page):
            page_obj = gl.page_manager.get_page(default_page, deck_controller=controller)
            if page_obj:
                controller.load_page(page_obj)
                self._check_stop_audio()
                return

        # Fallback to first non-DeckWave user page
        for p in gl.page_manager.get_pages():
            if os.path.isfile(p) and not ("DeckWave" in os.path.basename(p)):
                page_obj = gl.page_manager.get_page(p, deck_controller=controller)
                if page_obj:
                    controller.load_page(page_obj)
                    self._check_stop_audio()
                    return

    def _check_stop_audio(self):
        """Stops the audio capture service if no visualizer page is active on any deck."""
        controllers = getattr(gl.deck_manager, "deck_controller", [])
        any_visualizer_active = False
        for c in controllers:
            active_page = getattr(c, "active_page", None)
            if active_page:
                json_path = getattr(active_page, "json_path", "")
                if "DeckWave" in os.path.basename(str(json_path)):
                    any_visualizer_active = True
                    break

        if not any_visualizer_active:
            self.audio_service.stop()

    def get_global_settings(self) -> dict:
        """Retrieves visualizer settings from plugin settings or launcher defaults."""
        defaults = {
            "audio_source": "default",
            "gain": 1.2,
            "decay_speed": "normal",
            "bar_style": "solid",
            "color_mode": "3-color",
            "color_stops": ["#00FF88", "#00D4FF", "#9D00FF", "#FF0088", "#FF3300"],
            "bg_color": "#121216",
            "peak_color": "#FFFFFF"
        }
        # In StreamController, plugin-level settings or launcher settings can be stored
        plugin_settings = self.get_settings() if hasattr(self, "get_settings") else {}
        defaults.update(plugin_settings)
        return defaults
