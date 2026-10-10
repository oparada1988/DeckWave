"""
DeckWave Return Action
======================
Dedicated exit key that returns to the previous Stream Deck page.
"""

import os
from src.backend.PluginManager.ActionBase import ActionBase

class DeckWaveReturnAction(ActionBase):
    def __init__(self, action_id: str, action_name: str, deck_controller, page, plugin_base, state: int, input_ident):
        super().__init__(action_id, action_name, deck_controller, page, plugin_base, state, input_ident)

    def on_ready(self):
        icon_path = os.path.join(self.plugin_base.PATH, "assets", "action_return.png")
        if os.path.isfile(icon_path):
            self.set_media(media_path=icon_path)

    def on_key_down(self):
        if hasattr(self.plugin_base, "return_to_origin"):
            self.plugin_base.return_to_origin(self.deck_controller)
