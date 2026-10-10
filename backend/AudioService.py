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

        # FFT settings
        self.sample_rate = 44100
        self.chunk_size = 2048
        self.window = np.hanning(self.chunk_size)

        # Falloff rates per frame (~20 FPS)
        self.falloff_rates = {
            "fast": 0.08,
            "normal": 0.045,
            "slow": 0.025
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
        Enumerates active audio application streams from PipeWire.
        Returns a list of dicts: [{"id": str, "name": str, "app": str}]
        """
        apps = []
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

                    # We are looking for audio playback streams
                    if "Stream/Output/Audio" in media_class and app_name and app_name not in seen_names:
                        seen_names.add(app_name)
                        apps.append({
                            "id": str(node_id),
                            "name": app_name,
                            "app": props.get("application.name", app_name)
                        })
        except Exception as e:
            log.debug(f"Could not query pw-dump: {e}")

        return apps

    def _get_record_command(self) -> list[str]:
        """Constructs the pw-record command."""
        cmd = [
            "pw-record",
            "--format=s16le",
            f"--rate={self.sample_rate}",
            "--channels=1"
        ]

        # Target specific application node or default output monitor
        if self.target_source and self.target_source != "default":
            cmd.extend(["--target", str(self.target_source)])

        cmd.append("-")  # output raw PCM to stdout
        return cmd

    def _capture_loop(self):
        """Background loop reading PCM data and computing FFT bands."""
        cmd = self._get_record_command()
        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=self.chunk_size * 2
            )
        except Exception as e:
            log.error(f"Failed to start pw-record: {e}")
            self.running = False
            return

        bytes_to_read = self.chunk_size * 2  # 16-bit = 2 bytes per sample

        while self.running and self.process and self.process.poll() is None:
            try:
                raw_data = self.process.stdout.read(bytes_to_read)
                if not raw_data or len(raw_data) < bytes_to_read:
                    # Apply decay on silence or underrun
                    self._decay_all()
                    time.sleep(0.03)
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

                # Cap loop at ~30 FPS to save CPU
                time.sleep(0.015)

            except Exception as e:
                log.debug(f"Audio capture loop exception: {e}")
                time.sleep(0.05)

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

        # Logarithmic frequency ranges: ~40 Hz to ~16,000 Hz
        min_freq = 40.0
        max_freq = 15000.0

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

            # Bass boost & high-end equalization compensation
            # High frequencies naturally carry less amplitude in music
            high_boost = 1.0 + (i / float(num_bands)) * 2.2
            val *= high_boost

            # Apply user sensitivity gain
            val *= self.gain

            # Non-linear perceptual scaling (power law)
            norm_val = math.pow(min(1.0, val * 4.5), 0.75)
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
