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
        b = int(c1[2] + (c2[2] - c1[2]) * t)
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

    def get_cached_gradient_strip(
        self,
        key_width: int,
        total_rows: int,
        key_height: int,
        stops_rgb: tuple[tuple[int, int, int], ...],
        bar_margin: int
    ) -> Image.Image:
        """
        Pre-renders and caches the entire vertical column gradient strip across all rows.
        This turns per-frame line-by-line drawing into instantaneous fast memory crops/pastes!
        """
        total_height = total_rows * key_height
        cache_key = (key_width, total_height, stops_rgb, bar_margin)
        if cache_key in self._gradient_cache:
            return self._gradient_cache[cache_key]

        strip = Image.new("RGBA", (key_width, total_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(strip)
        bar_x0 = bar_margin
        bar_x1 = key_width - bar_margin - 1

        for y in range(total_height):
            # 0 is top of the deck, total_height - 1 is bottom
            ratio = (total_height - 1 - y) / float(max(1, total_height - 1))
            color = self.get_color_at_height(ratio, list(stops_rgb))
            draw.line([(bar_x0, y), (bar_x1, y)], fill=color)

        self._gradient_cache[cache_key] = strip
        return strip

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
        bar_margin: int = 4,
        show_peaks: bool = True
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
            # Continuous gradient bar using pre-rendered strip
            if fill_pixels > 0:
                stops_tuple = tuple(stops_rgb)
                full_strip = self.get_cached_gradient_strip(
                    key_width=key_width,
                    total_rows=total_rows,
                    key_height=key_height,
                    stops_rgb=stops_tuple,
                    bar_margin=bar_margin
                )
                # Calculate global vertical slice for this row:
                # row 0 is top row (y: 0 to key_height)
                # row total_rows-1 is bottom row
                cell_strip_y0 = row * key_height
                active_strip_y0 = cell_strip_y0 + (key_height - fill_pixels)
                active_strip_y1 = cell_strip_y0 + key_height

                active_slice = full_strip.crop((0, active_strip_y0, key_width, active_strip_y1))
                img.paste(active_slice, (0, key_height - fill_pixels), active_slice)

        # Peak indicator line (if enabled and peak falls within this key)
        if show_peaks and peak_level > 0.0 and (row_min_level <= peak_level <= row_max_level):
            peak_fill = (peak_level - row_min_level) / row_height_span
            peak_y = int(key_height - 1 - (peak_fill * (key_height - 1)))
            peak_y = max(1, min(key_height - 2, peak_y))
            # Draw 2px bright peak tick
            draw.line([(bar_x0, peak_y), (bar_x1, peak_y)], fill=peak_rgb, width=2)

        return img

    def render_return_cell(
        self,
        key_width: int,
        key_height: int,
        bg_color_hex: str = "#121216",
        arrow_color_hex: str = "#FFFFFF",
        opacity: float = 1.0
    ) -> Image.Image:
        """
        Renders a clean return/back key button for the Stream Deck cell when idle,
        with optional opacity (0.0 invisible -> 1.0 fully opaque) for smooth fade animations.
        """
        bg_rgb = self.hex_to_rgb(bg_color_hex)
        target_arrow_rgb = self.hex_to_rgb(arrow_color_hex)

        clamped_opacity = max(0.0, min(1.0, opacity))
        arrow_rgb = self.interpolate_color(bg_rgb, target_arrow_rgb, clamped_opacity)

        img = Image.new("RGB", (key_width, key_height), bg_rgb)
        draw = ImageDraw.Draw(img)

        # Draw a clean centered return arrow:  <--
        cx = key_width // 2
        cy = key_height // 2
        size = int(min(key_width, key_height) * 0.22)

        # Arrow shaft
        draw.line([(cx - size, cy), (cx + size, cy)], fill=arrow_rgb, width=max(2, size // 3))
        # Arrow left head
        draw.line([(cx - size, cy), (cx - size // 2, cy - size // 2)], fill=arrow_rgb, width=max(2, size // 3))
        draw.line([(cx - size, cy), (cx - size // 2, cy + size // 2)], fill=arrow_rgb, width=max(2, size // 3))

        return img
