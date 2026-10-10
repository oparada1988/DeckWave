"""
DeckWave Audio Capture & Spectrum Analysis Service
==================================================
Captures audio streams via PipeWire (pw-record) and performs real-time
FFT spectrum analysis with logarithmic frequency binning, ballistics,
and peak hold tracking.
"""

import subprocess
import threading
import time
import json
import math
import numpy as np
from loguru import logger as log

class AudioService:
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = AudioService()
            return cls._instance

    def __init__(self):
        self.running = False
        self.thread = None
        self.process = None

        # Settings
        self.target_source = "default"  # "default" or app node name/id
        self.gain = 1.2
        self.decay_speed = "normal"  # "fast", "normal", "slow"

        # Spectrum State: cached for different column counts (4, 5, 8)
        self._levels = {}
        self._peaks = {}
        self._peak_hold_times = {}
        self._state_lock = threading.Lock()

        # FFT settings: 1024 samples @ 44.1kHz = ~23ms latency (fast responsive FFT)
        self.sample_rate = 44100
        self.chunk_size = 1024
        self.window = np.hanning(self.chunk_size)

        # Falloff rates per frame (~30 FPS rendering)
        self.falloff_rates = {
            "fast": 0.09,
            "normal": 0.05,
            "slow": 0.028
        }

    def set_settings(self, target_source: str = "default", gain: float = 1.2, decay_speed: str = "normal"):
        """Update active settings."""
        restart_needed = (target_source != self.target_source and self.running)
        self.target_source = target_source
        self.gain = max(0.2, min(5.0, gain))
        if decay_speed in self.falloff_rates:
            self.decay_speed = decay_speed

        if restart_needed:
            self.restart()

    def start(self):
        """Starts audio capture if not already running."""
        with self._lock:
            if self.running:
                return
            self.running = True
            self.thread = threading.Thread(target=self._capture_loop, name="DeckWaveAudioCapture", daemon=True)
            self.thread.start()
            log.info("DeckWave AudioService started.")

    def stop(self):
        """Stops audio capture."""
        with self._lock:
            self.running = False
            if self.process:
                try:
                    self.process.terminate()
                except Exception:
                    pass
                self.process = None
            log.info("DeckWave AudioService stopped.")

    def restart(self):
        """Restarts the audio capture process."""
        self.stop()
        time.sleep(0.1)
        self.start()

    @staticmethod
    def get_active_audio_apps() -> list[dict]:
        """
        Enumerates active audio application streams and hardware/virtual sources.
        Uses pactl (compatible with Flatpak sandbox and native PipeWire/PulseAudio)
        with fallback to pw-dump.
        Returns a list of dicts: [{"id": str, "name": str, "type": str}]
        """
        import re
        targets = []

        # 1. Query PulseAudio/PipeWire sink-inputs (active playback streams) via pactl
        try:
            res_inputs = subprocess.run(["pactl", "list", "sink-inputs"], capture_output=True, text=True, timeout=2.0)
            if res_inputs.returncode == 0 and res_inputs.stdout:
                blocks = res_inputs.stdout.split("Sink Input #")
                for b in blocks[1:]:
                    lines = b.splitlines()
                    stream_idx = lines[0].strip()
                    props = {}
                    for line in lines[1:]:
                        m = re.match(r'^\s*([a-zA-Z0-9._-]+)\s*=\s*\"(.*)\"$', line)
                        if m:
                            props[m.group(1)] = m.group(2)
                        elif ":" in line and not line.strip().startswith("Properties:"):
                            k, v = line.split(":", 1)
                            props[k.strip()] = v.strip()

                    app_name = props.get("application.name")
                    node_name = props.get("node.name", "")
                    media_name = props.get("media.name", "")
                    desc = props.get("device.description", "")

                    if app_name:
                        display = app_name
                    elif "WaveController_submix_" in node_name:
                        sub_part = node_name.split("WaveController_submix_", 1)[1]
                        parts = sub_part.split("_")
                        ch = parts[0].capitalize()
                        mix = parts[1].capitalize() if len(parts) > 1 else ""
                        display = f"WaveController: {ch} ({mix})"
                    elif media_name:
                        display = media_name
                    elif desc:
                        display = desc
                    else:
                        display = f"Stream #{stream_idx}"

                    targets.append({
                        "id": f"stream:{stream_idx}",
                        "name": f"App: {display}",
                        "type": "stream"
                    })
        except Exception as e:
            log.debug(f"Could not query pactl sink-inputs: {e}")

        # 2. Query PulseAudio/PipeWire sources (hardware devices and virtual mix monitors) via pactl
        try:
            res_sources = subprocess.run(["pactl", "list", "sources"], capture_output=True, text=True, timeout=2.0)
            if res_sources.returncode == 0 and res_sources.stdout:
                blocks_s = res_sources.stdout.split("Source #")
                for b in blocks_s[1:]:
                    lines = b.splitlines()
                    curr_name = ""
                    curr_desc = ""
                    for line in lines[1:]:
                        line_s = line.strip()
                        if line_s.startswith("Name:"):
                            curr_name = line_s.split("Name:", 1)[1].strip()
                        elif line_s.startswith("Description:"):
                            curr_desc = line_s.split("Description:", 1)[1].strip()

                    if curr_name:
                        is_monitor = curr_name.endswith(".monitor")
                        desc = curr_desc or curr_name
                        label = f"Monitor: {desc}" if is_monitor else f"Input: {desc}"
                        targets.append({
                            "id": f"source:{curr_name}",
                            "name": label,
                            "type": "source"
                        })
        except Exception as e:
            log.debug(f"Could not query pactl sources: {e}")

        # 3. Fallback to pw-dump if pactl produced nothing (native environments without pactl)
        if not targets:
            try:
                res = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=2.0)
                if res.returncode == 0 and res.stdout:
                    data = json.loads(res.stdout)
                    seen_names = set()
                    for item in data:
                        props = item.get("info", {}).get("props", {})
                        media_class = props.get("media.class", "")
                        app_name = props.get("application.name") or props.get("node.name")
                        node_id = item.get("id")

                        if "Stream/Output/Audio" in media_class and app_name and app_name not in seen_names:
                            seen_names.add(app_name)
                            targets.append({
                                "id": f"pw:{node_id}",
                                "name": f"App: {app_name}",
                                "type": "stream"
                            })
            except Exception as e:
                log.debug(f"Could not query pw-dump: {e}")

        return targets

    def _get_record_command(self) -> list[str]:
        """Constructs the audio recording command using parec (or pw-record as fallback)."""
        target = self.target_source or "default"

        # Check if parec is available (standard in Flatpak with pulseaudio socket)
        # Using parec with 20ms low latency to eliminate buffering stutter:
        cmd = [
            "parec",
            "--raw",
            f"--rate={self.sample_rate}",
            "--channels=1",
            "--latency-msec=20"
        ]

        if target.startswith("stream:"):
            # Target specific sink-input index: parec --monitor-stream=INDEX
            stream_idx = target.split("stream:", 1)[1]
            cmd.append(f"--monitor-stream={stream_idx}")
        elif target.startswith("source:"):
            # Target specific source or monitor device: parec -d SOURCE_NAME
            src_name = target.split("source:", 1)[1]
            cmd.extend(["-d", src_name])
        elif target == "default" or not target:
            # Default monitor for system audio playback: @DEFAULT_MONITOR@
            # Note: parec without -d defaults to recording from the default *source* (microphone)
            cmd.extend(["-d", "@DEFAULT_MONITOR@"])
        elif target.startswith("pw:"):
            # Fallback to pw-record for native pipewire node IDs
            node_id = target.split("pw:", 1)[1]
            return [
                "pw-record",
                "--format=s16le",
                f"--rate={self.sample_rate}",
                "--channels=1",
                "--target", node_id,
                "-"
            ]
        else:
            # Treat raw string as device name if possible
            cmd.extend(["-d", target])

        return cmd

    def _capture_loop(self):
        """Background loop reading PCM data and computing FFT bands."""
        cmd = self._get_record_command()
        log.info(f"Starting DeckWave capture process: {' '.join(cmd)}")
        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=self.chunk_size * 2
            )
        except Exception as e:
            log.error(f"Failed to start audio recording process ({cmd[0]}): {e}")
            self.running = False
            return

        bytes_to_read = self.chunk_size * 2  # 16-bit = 2 bytes per sample

        while self.running and self.process and self.process.poll() is None:
            try:
                raw_data = self.process.stdout.read(bytes_to_read)
                if not raw_data or len(raw_data) < bytes_to_read:
                    # Apply decay on silence or underrun
                    self._decay_all()
                    time.sleep(0.02)
                    continue

                # Convert raw PCM bytes to numpy int16 array
                audio_samples = np.frombuffer(raw_data, dtype=np.int16).astype(np.float32) / 32768.0

                # Windowed real-FFT
                windowed = audio_samples * self.window
                fft_mag = np.abs(np.fft.rfft(windowed))

                # Normalize FFT magnitudes
                fft_mag = fft_mag / (self.chunk_size / 2)

                # Compute bands for 4, 5, and 8 columns
                for num_bands in (4, 5, 8):
                    band_amps = self._compute_frequency_bands(fft_mag, num_bands)
                    self._update_levels(num_bands, band_amps)

            except Exception as e:
                log.debug(f"Audio capture loop exception: {e}")
                time.sleep(0.02)

        if self.process:
            try:
                self.process.terminate()
            except Exception:
                pass
            self.process = None

    def _compute_frequency_bands(self, fft_mag: np.ndarray, num_bands: int) -> list[float]:
        """Groups FFT frequency bins logarithmically into num_bands normalized levels."""
        n_bins = len(fft_mag)
        freq_step = (self.sample_rate / 2.0) / n_bins

        # Logarithmic frequency ranges: ~30 Hz to ~16,000 Hz
        min_freq = 30.0
        max_freq = 16000.0

        # Calculate logarithmic frequency cutoffs
        log_cutoffs = np.geomspace(min_freq, max_freq, num_bands + 1)
        band_values = []

        for i in range(num_bands):
            f_low = log_cutoffs[i]
            f_high = log_cutoffs[i + 1]

            bin_low = max(0, int(f_low / freq_step))
            bin_high = min(n_bins - 1, max(bin_low + 1, int(f_high / freq_step)))

            # Average magnitude across this frequency range
            mag_slice = fft_mag[bin_low:bin_high]
            val = float(np.mean(mag_slice)) if len(mag_slice) > 0 else 0.0

            # Frequency curve compensation: high frequencies naturally have less energy
            # Boost progressively from lower mids to highs
            eq_boost = 1.0 + (i / float(num_bands)) * 2.8
            val *= eq_boost

            # Apply sensitivity gain
            val *= (self.gain * 1.5)

            # Perceptual logarithmic-like response (square root / power scaling)
            # Produces lively, energetic bar movement across typical music mixing levels
            norm_val = math.pow(min(1.0, val * 3.8), 0.65)
            band_values.append(max(0.0, min(1.0, norm_val)))

        return band_values

    def _update_levels(self, num_bands: int, new_amps: list[float]):
        """Applies ballistics (instant attack, smooth decay) and peak hold logic."""
        with self._state_lock:
            now = time.time()
            if num_bands not in self._levels:
                self._levels[num_bands] = [0.0] * num_bands
                self._peaks[num_bands] = [0.0] * num_bands
                self._peak_hold_times[num_bands] = [0.0] * num_bands

            curr_levels = self._levels[num_bands]
            curr_peaks = self._peaks[num_bands]
            peak_times = self._peak_hold_times[num_bands]
            falloff = self.falloff_rates.get(self.decay_speed, 0.045)

            for i in range(num_bands):
                target = new_amps[i]
                current = curr_levels[i]

                # Ballistics: instant attack, smooth exponential decay
                if target >= current:
                    curr_levels[i] = target
                else:
                    curr_levels[i] = max(0.0, current - falloff)

                # Peak hold tracking
                if curr_levels[i] >= curr_peaks[i]:
                    curr_peaks[i] = curr_levels[i]
                    peak_times[i] = now + 0.35  # hold for 350ms
                elif now > peak_times[i]:
                    # Peak falls with gravity
                    curr_peaks[i] = max(curr_levels[i], curr_peaks[i] - (falloff * 0.75))

    def _decay_all(self):
        """Smoothly decays all bars toward zero during silence."""
        with self._state_lock:
            falloff = self.falloff_rates.get(self.decay_speed, 0.045)
            for num_bands in self._levels:
                for i in range(num_bands):
                    self._levels[num_bands][i] = max(0.0, self._levels[num_bands][i] - falloff)
                    self._peaks[num_bands][i] = max(0.0, self._peaks[num_bands][i] - (falloff * 0.75))

    def get_band_levels(self, num_bands: int) -> tuple[list[float], list[float]]:
        """Returns (levels, peaks) for the requested column count."""
        with self._state_lock:
            if num_bands not in self._levels:
                return ([0.0] * num_bands, [0.0] * num_bands)
            return (list(self._levels[num_bands]), list(self._peaks[num_bands]))
