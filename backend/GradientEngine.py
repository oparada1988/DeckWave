"""
DeckWave Gradient and Rendering Engine
=====================================
Generates multi-stop vertical color gradients (1 to 5 stops) and renders
column audio visualizer slices for Stream Deck keys.
"""

from PIL import Image, ImageDraw
import math

class GradientEngine:
    def __init__(self):
        self._gradient_cache = {}

    @staticmethod
    def hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
        """Convert a hex color string (#RRGGBB or #RGB) to (r, g, b)."""
        hex_str = hex_str.lstrip("#")
        if len(hex_str) == 3:
            hex_str = "".join([c * 2 for c in hex_str])
        try:
            val = int(hex_str, 16)
            return ((val >> 16) & 255, (val >> 8) & 255, val & 255)
        except Exception:
            return (0, 255, 128)

    @staticmethod
    def interpolate_color(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> tuple[int, int, int]:
        """Linearly interpolate between two RGB colors by factor t (0.0 to 1.0)."""
        t = max(0.0, min(1.0, t))
        r = int(c1[0] + (c2[0] - c1[0]) * t)
        g = int(c1[1] + (c2[1] - c1[1]) * t)
        b = int(c2[2] + (c2[2] - c1[2]) * t)
        return (r, g, b)

    def get_color_at_height(self, ratio: float, stops: list[tuple[int, int, int]]) -> tuple[int, int, int]:
        """
        Get the color along a multi-stop gradient at normalized height ratio (0.0 bottom -> 1.0 top).
        """
        if not stops:
            return (0, 255, 128)
        if len(stops) == 1:
            return stops[0]

        ratio = max(0.0, min(1.0, ratio))
        n_segments = len(stops) - 1
        pos = ratio * n_segments
        idx = int(pos)
        if idx >= n_segments:
            return stops[-1]
        t = pos - idx
        return self.interpolate_color(stops[idx], stops[idx + 1], t)

    def render_key_cell(
        self,
        col: int,
        row: int,
        total_rows: int,
        key_width: int,
        key_height: int,
        level: float,
        peak_level: float,
        color_mode: str,
        color_stops: list[str],
        bg_color_hex: str = "#141416",
        peak_color_hex: str = "#FFFFFF",
        bar_style: str = "solid",
        bar_margin: int = 4
    ) -> Image.Image:
        """
        Renders a single key's portion of an audio bar.
        
        Args:
            col: Column index (0 to cols-1)
            row: Row index (0 = top, total_rows-1 = bottom)
            total_rows: Total rows in the deck grid (e.g. 4 on XL, 3 on MK2)
            key_width, key_height: Pixel dimensions of key (e.g. 96x96, 72x72)
            level: Normalized audio level for this column (0.0 to 1.0)
            peak_level: Normalized peak hold level (0.0 to 1.0)
            color_mode: "1-color", "2-color", "3-color", "4-color", "5-color"
            color_stops: List of hex colors for the gradient
            bg_color_hex: Background color for empty area
            peak_color_hex: Color of the peak line
            bar_style: "solid", "stepped", or "peak"
            bar_margin: Horizontal inset padding for the bar
        """
        # Parse colors
        stops_rgb = [self.hex_to_rgb(c) for c in color_stops]
        bg_rgb = self.hex_to_rgb(bg_color_hex)
        peak_rgb = self.hex_to_rgb(peak_color_hex)

        # Invert row so 0 is bottom and total_rows-1 is top for calculation
        row_from_bottom = (total_rows - 1) - row
        row_height_span = 1.0 / total_rows
        row_min_level = row_from_bottom * row_height_span
        row_max_level = (row_from_bottom + 1) * row_height_span

        # Create base image
        img = Image.new("RGB", (key_width, key_height), bg_rgb)
        draw = ImageDraw.Draw(img)

        # Bar horizontal coordinates
        bar_x0 = bar_margin
        bar_x1 = key_width - bar_margin - 1

        # Calculate fill ratio within THIS specific key (0.0 to 1.0)
        if level <= row_min_level:
            key_fill = 0.0
        elif level >= row_max_level:
            key_fill = 1.0
        else:
            key_fill = (level - row_min_level) / row_height_span

        fill_pixels = int(round(key_fill * key_height))

        if bar_style == "stepped":
            # Segmented LED blocks (e.g. 8 segments per key)
            num_segments = 8
            segment_gap = 2
            total_gaps = (num_segments - 1) * segment_gap
            segment_h = max(2, (key_height - total_gaps) // num_segments)

            # Draw inactive faint segments
            faint_bg = tuple(max(0, int(c * 0.4)) for c in bg_rgb)
            for seg in range(num_segments):
                # seg 0 is bottom, seg 7 is top
                y_bottom = key_height - (seg * (segment_h + segment_gap))
                y_top = y_bottom - segment_h
                draw.rectangle([bar_x0, y_top, bar_x1, y_bottom], fill=faint_bg)

            # Draw active lit segments
            active_segs = int(math.ceil(key_fill * num_segments))
            for seg in range(active_segs):
                y_bottom = key_height - (seg * (segment_h + segment_gap))
                y_top = y_bottom - segment_h

                # Calculate overall vertical gradient ratio (0.0 at very bottom of deck to 1.0 at very top)
                seg_global_ratio = row_min_level + ((seg + 0.5) / num_segments) * row_height_span
                color = self.get_color_at_height(seg_global_ratio, stops_rgb)
                draw.rectangle([bar_x0, y_top, bar_x1, y_bottom], fill=color)

        else:
            # Continuous gradient bar
            if fill_pixels > 0:
                # Render line by line for smooth vertical gradient
                for py in range(key_height - fill_pixels, key_height):
                    # Inverted: py = key_height-1 is bottom of this key
                    py_ratio = (key_height - 1 - py) / float(key_height)
                    global_ratio = row_min_level + (py_ratio * row_height_span)
                    color = self.get_color_at_height(global_ratio, stops_rgb)
                    draw.line([(bar_x0, py), (bar_x1, py)], fill=color)

        # Peak indicator line (if peak falls within this key)
        if peak_level > 0.0 and (row_min_level <= peak_level <= row_max_level):
            peak_fill = (peak_level - row_min_level) / row_height_span
            peak_y = int(key_height - 1 - (peak_fill * (key_height - 1)))
            peak_y = max(1, min(key_height - 2, peak_y))
            # Draw 2px bright peak tick
            draw.line([(bar_x0, peak_y), (bar_x1, peak_y)], fill=peak_rgb, width=2)

        return img
