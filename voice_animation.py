"""CPU-optimized, click-through PySide6 waveform overlay for Windows.

Call ``create_waveform()`` once from the Qt GUI thread, then call
``display_amplitude(value)`` with values from 0.0 to 1.0. Amplitude updates may
come from a worker/audio thread after the overlay has been created.

The renderer intentionally trades a small amount of curve detail for much lower
CPU usage on large displays and transparent Windows overlays.
"""

from __future__ import annotations

import math
import sys
import threading
from dataclasses import dataclass

from PySide6.QtCore import QElapsedTimer, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QWidget


# ---------------------------------------------------------------------------
# Windows helpers
# ---------------------------------------------------------------------------

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)

    _get_window = _user32.GetWindow
    _get_window.argtypes = (wintypes.HWND, wintypes.UINT)
    _get_window.restype = wintypes.HWND

    _get_class_name = _user32.GetClassNameW
    _get_class_name.argtypes = (
        wintypes.HWND,
        wintypes.LPWSTR,
        ctypes.c_int,
    )
    _get_class_name.restype = ctypes.c_int

    _set_window_pos = _user32.SetWindowPos
    _set_window_pos.argtypes = (
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    )
    _set_window_pos.restype = wintypes.BOOL

    _get_window_long_ptr = _user32.GetWindowLongPtrW
    _get_window_long_ptr.argtypes = (wintypes.HWND, ctypes.c_int)
    _get_window_long_ptr.restype = ctypes.c_ssize_t

    _set_window_long_ptr = _user32.SetWindowLongPtrW
    _set_window_long_ptr.argtypes = (
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_ssize_t,
    )
    _set_window_long_ptr.restype = ctypes.c_ssize_t

    _GW_HWNDPREV = 3
    _GWL_EXSTYLE = -20

    _HWND_TOPMOST = wintypes.HWND(-1)

    _SWP_NOSIZE = 0x0001
    _SWP_NOMOVE = 0x0002
    _SWP_NOACTIVATE = 0x0010
    _SWP_SHOWWINDOW = 0x0040
    _SWP_NOOWNERZORDER = 0x0200
    _TOPMOST_FLAGS = (
        _SWP_NOSIZE
        | _SWP_NOMOVE
        | _SWP_NOACTIVATE
        | _SWP_SHOWWINDOW
        | _SWP_NOOWNERZORDER
    )

    _WS_EX_TRANSPARENT = 0x00000020
    _WS_EX_TOOLWINDOW = 0x00000080
    _WS_EX_LAYERED = 0x00080000
    _WS_EX_NOACTIVATE = 0x08000000

    _TASKBAR_CLASSES = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd"}


# ---------------------------------------------------------------------------
# Rendering configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WaveStyle:
    cycles: float
    speed: float
    amplitude_scale: float
    phase_offset: float
    fill_color: QColor
    line_color: QColor | None = None


_GREEN_WAVE = WaveStyle(
    cycles=3.0,
    speed=2.35,
    amplitude_scale=1.0,
    phase_offset=0.0,
    fill_color=QColor(35, 210, 120, 153),
    line_color=QColor(80, 255, 165, 235),
)

# The rear yellow wave is fill-only. Removing one antialiased outline lowers
# paint cost while the green foreground outline keeps the composition crisp.
_YELLOW_WAVE = WaveStyle(
    cycles=4.2,
    speed=-1.70,
    amplitude_scale=0.78,
    phase_offset=1.1,
    fill_color=QColor(205, 205, 45, 235),
    
)


_overlay: AnimationWindow | None = None


class AnimationWindow(QWidget):
    """Transparent waveform window placed over the bottom of the display."""

    # A parameterless wake signal avoids queuing every microphone sample. The
    # actual peak value is aggregated behind a lock and consumed once per frame.
    amplitude_available = Signal()

    FRAME_INTERVAL_MS = 33          # Approximately 30 FPS.
    SAMPLE_HOLD_MS = 220            # Bridges normal gaps between audio samples.
    MAX_FRAME_DELTA_SECONDS = 0.12  # Keeps motion time-based after delayed frames.

    SILENCE_FLOOR = 0.025           # Ignore background noise and zero samples.
    TARGET_POINT_COUNT = 160        # Previously about 300 points per wave.

    OVERLAY_HEIGHT_RATIO = 0.065
    MIN_OVERLAY_HEIGHT = 70
    MAX_OVERLAY_HEIGHT = 110

    IDLE_AMPLITUDE = 0.035
    IDLE_STOP_THRESHOLD = 0.01
    ATTACK_RATE = 15.0
    RELEASE_RATE = 5.0
    LISTENING_FADE_RATE = 7.0

    def __init__(self) -> None:
        super().__init__()

        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowTransparentForInput
            | Qt.WindowDoesNotAcceptFocus
            | Qt.NoDropShadowWindowHint
        )

        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)

        # Multiple worker-thread samples are collapsed into one peak amplitude
        # for the next rendered frame. This bounds signal and GUI work to FPS.
        self._pending_lock = threading.Lock()
        self._pending_peak: float | None = None
        self._wake_queued = False
        self.amplitude_available.connect(
            self._wake_for_pending_amplitude,
            Qt.QueuedConnection,
        )

        self._clock = QElapsedTimer()
        self._clock.start()
        self._last_frame_ms = self._clock.elapsed()
        self._last_sample_ms = -10_000

        self._target_amplitude = 0.0
        self._smoothed_amplitude = 0.0
        self._voice_mix = 0.0

        # Motion is independent of amplitude, so loudness cannot alter speed.
        self._motion_time = 0.0

        # Cached values are rebuilt only when the overlay is resized.
        self._wave_points: list[tuple[float, float, float, float]] = []

        self._green_pen = QPen(_GREEN_WAVE.line_color, 2.0)
        self._green_pen.setCapStyle(Qt.RoundCap)
        self._green_pen.setJoinStyle(Qt.RoundJoin)

        self._animation_timer = QTimer(self)
        self._animation_timer.setTimerType(Qt.CoarseTimer)
        self._animation_timer.setInterval(self.FRAME_INTERVAL_MS)
        self._animation_timer.timeout.connect(self._next_frame)
        # Do not start here. QWidget's initial paint displays the idle wave, and
        # the timer starts only when a real voice sample crosses the noise floor.

        self._taskbar_monitor: QTimer | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def display_amplitude(self, amplitude_scalar: float) -> None:
        """Aggregate an amplitude sample safely from any Python thread."""
        try:
            amplitude = float(amplitude_scalar)
        except (TypeError, ValueError):
            return

        amplitude = max(0.0, min(1.0, amplitude))

        # Continuous zero/background samples must not keep the transparent
        # overlay repainting forever.
        if amplitude < self.SILENCE_FLOOR:
            return

        should_emit_wake = False
        with self._pending_lock:
            if self._pending_peak is None:
                self._pending_peak = amplitude
            else:
                self._pending_peak = max(self._pending_peak, amplitude)

            # Keep at most one queued wake signal before the next frame. All
            # additional samples are folded into _pending_peak.
            if not self._wake_queued:
                self._wake_queued = True
                should_emit_wake = True

        if should_emit_wake:
            self.amplitude_available.emit()

    def fit_to_screen(self, screen_geometry) -> None:
        """Cover the physical bottom edge, including the taskbar region."""
        wave_height = min(
            max(
                round(screen_geometry.height() * self.OVERLAY_HEIGHT_RATIO),
                self.MIN_OVERLAY_HEIGHT,
            ),
            self.MAX_OVERLAY_HEIGHT,
        )

        self.setGeometry(
            screen_geometry.x(),
            screen_geometry.bottom() - wave_height + 1,
            screen_geometry.width(),
            wave_height,
        )

    def start_taskbar_monitor(self) -> None:
        """Keep the overlay above a taskbar that Windows raises later."""
        if sys.platform != "win32":
            return

        self._apply_windows_overlay_style()

        # This is intentionally low frequency; walking the Z-order is unrelated
        # to animation and does not need to happen per frame.
        self._taskbar_monitor = QTimer(self)
        self._taskbar_monitor.setTimerType(Qt.VeryCoarseTimer)
        self._taskbar_monitor.setInterval(1000)
        self._taskbar_monitor.timeout.connect(self._restore_taskbar_z_order)
        self._taskbar_monitor.start()

    # ------------------------------------------------------------------
    # Input and animation
    # ------------------------------------------------------------------

    @Slot()
    def _wake_for_pending_amplitude(self) -> None:
        """Start rendering when the first pending voice sample arrives."""
        if not self._animation_timer.isActive():
            self._last_frame_ms = self._clock.elapsed()
            self._animation_timer.start()

    def _take_pending_peak(self) -> float | None:
        """Consume the loudest sample received since the previous frame."""
        with self._pending_lock:
            amplitude = self._pending_peak
            self._pending_peak = None
            self._wake_queued = False
        return amplitude

    def _next_frame(self) -> None:
        now_ms = self._clock.elapsed()

        pending_amplitude = self._take_pending_peak()
        if pending_amplitude is not None:
            self._target_amplitude = pending_amplitude
            self._last_sample_ms = now_ms

        elapsed_seconds = max(
            0.0,
            min(
                (now_ms - self._last_frame_ms) / 1000.0,
                self.MAX_FRAME_DELTA_SECONDS,
            ),
        )
        self._last_frame_ms = now_ms

        sample_is_fresh = (
            now_ms - self._last_sample_ms <= self.SAMPLE_HOLD_MS
        )

        desired_amplitude = (
            self._target_amplitude if sample_is_fresh else 0.0
        )
        desired_voice_mix = 1.0 if sample_is_fresh else 0.0

        self._voice_mix = self._smooth_toward(
            current=self._voice_mix,
            target=desired_voice_mix,
            rate=self.LISTENING_FADE_RATE,
            elapsed_seconds=elapsed_seconds,
        )

        amplitude_rate = (
            self.ATTACK_RATE
            if desired_amplitude > self._smoothed_amplitude
            else self.RELEASE_RATE
        )
        self._smoothed_amplitude = self._smooth_toward(
            current=self._smoothed_amplitude,
            target=desired_amplitude,
            rate=amplitude_rate,
            elapsed_seconds=elapsed_seconds,
        )

        # Elapsed time—not timer callback count—controls wave speed.
        self._motion_time = math.fmod(
            self._motion_time + elapsed_seconds,
            10_000.0,
        )

        should_stop = (
            not sample_is_fresh
            and self._voice_mix < self.IDLE_STOP_THRESHOLD
            and self._smoothed_amplitude < self.IDLE_STOP_THRESHOLD
        )

        if should_stop:
            self._voice_mix = 0.0
            self._smoothed_amplitude = 0.0
            self._target_amplitude = 0.0
            self._animation_timer.stop()

        # Schedule one final clean idle frame even when the timer just stopped.
        self.update()

    @staticmethod
    def _smooth_toward(
        current: float,
        target: float,
        rate: float,
        elapsed_seconds: float,
    ) -> float:
        alpha = 1.0 - math.exp(-rate * elapsed_seconds)
        return current + ((target - current) * alpha)

    def _displayed_amplitude(self) -> float:
        voice_amplitude = (
            self._voice_mix
            * self._smoothed_amplitude
            * (1.0 - self.IDLE_AMPLITUDE)
        )
        return self.IDLE_AMPLITUDE + voice_amplitude

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        width = self.width()

        # Keep the path near TARGET_POINT_COUNT regardless of resolution.
        point_spacing = max(6, width // self.TARGET_POINT_COUNT)
        x_positions = list(range(0, width, point_spacing))
        if not x_positions or x_positions[-1] != width - 1:
            x_positions.append(width - 1)

        denominator = max(1, width - 1)
        self._wave_points = []

        for x in x_positions:
            horizontal_position = x / denominator
            edge_fade = max(
                0.0,
                math.sin(math.pi * horizontal_position),
            ) ** 0.45

            self._wave_points.append(
                (
                    float(x),
                    horizontal_position * math.tau * _GREEN_WAVE.cycles,
                    horizontal_position * math.tau * _YELLOW_WAVE.cycles,
                    edge_fade,
                )
            )

        line_width = min(max(self.height() * 0.012, 2.0), 3.5)
        self._green_pen.setWidthF(line_width)

        super().resizeEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self.width() <= 1 or self.height() <= 1:
            return

        painter = QPainter(self)

        # Explicitly replace the old translucent frame to avoid ghost pixels.
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

        amplitude = (self.height() - 2) * self._displayed_amplitude()

        # Paint the fill-only rear wave first, then the outlined foreground wave.
        self._draw_wave(
            painter=painter,
            style=_YELLOW_WAVE,
            pen=None,
            amplitude=amplitude,
            angle_index=2,
        )
        self._draw_wave(
            painter=painter,
            style=_GREEN_WAVE,
            pen=self._green_pen,
            amplitude=amplitude,
            angle_index=1,
        )

        painter.end()

    def _draw_wave(
        self,
        painter: QPainter,
        style: WaveStyle,
        pen: QPen | None,
        amplitude: float,
        angle_index: int,
    ) -> None:
        height = self.height()
        bottom_y = height - 1
        phase = (self._motion_time * style.speed) + style.phase_offset

        line_path = QPainterPath()

        for index, point in enumerate(self._wave_points):
            x = point[0]
            base_angle = point[angle_index]
            edge_fade = point[3]
            angle = base_angle + phase

            # One sine calculation per point instead of the previous two-sine
            # composite. The outline and overlapping waves retain visual detail.
            wave_value = 0.55 + (0.45 * math.sin(angle))

            y = bottom_y - (
                amplitude
                * style.amplitude_scale
                * wave_value
                * edge_fade
            )

            if index == 0:
                line_path.moveTo(x, y)
            else:
                line_path.lineTo(x, y)

        fill_path = QPainterPath(line_path)
        fill_path.lineTo(self.width() - 1, height)
        fill_path.lineTo(0, height)
        fill_path.closeSubpath()

        # Large translucent fills are rendered without antialiasing. Only the
        # visible foreground outline pays the antialiasing cost.
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.fillPath(fill_path, style.fill_color)

        if pen is not None:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.strokePath(line_path, pen)

    # ------------------------------------------------------------------
    # Windows taskbar/Z-order handling
    # ------------------------------------------------------------------

    def _apply_windows_overlay_style(self) -> None:
        if sys.platform != "win32":
            return

        handle = wintypes.HWND(int(self.winId()))
        current_style = _get_window_long_ptr(handle, _GWL_EXSTYLE)
        overlay_style = (
            current_style
            | _WS_EX_LAYERED
            | _WS_EX_TRANSPARENT
            | _WS_EX_TOOLWINDOW
            | _WS_EX_NOACTIVATE
        )
        _set_window_long_ptr(handle, _GWL_EXSTYLE, overlay_style)

        _set_window_pos(
            handle,
            _HWND_TOPMOST,
            0,
            0,
            0,
            0,
            _TOPMOST_FLAGS,
        )

    def _restore_taskbar_z_order(self) -> None:
        """Raise only when a Windows taskbar is actually above the overlay."""
        if sys.platform != "win32" or not self.isVisible():
            return

        overlay_handle = wintypes.HWND(int(self.winId()))
        window_above = _get_window(overlay_handle, _GW_HWNDPREV)
        class_name = ctypes.create_unicode_buffer(256)

        while window_above:
            if _get_class_name(window_above, class_name, len(class_name)):
                if class_name.value in _TASKBAR_CLASSES:
                    _set_window_pos(
                        overlay_handle,
                        _HWND_TOPMOST,
                        0,
                        0,
                        0,
                        0,
                        _TOPMOST_FLAGS,
                    )
                    return

            window_above = _get_window(window_above, _GW_HWNDPREV)


def create_waveform() -> AnimationWindow:
    """Create and show the shared waveform overlay once."""
    global _overlay

    if _overlay is not None:
        _overlay.show()
        _overlay.raise_()
        return _overlay

    app = QApplication.instance()
    if app is None:
        raise RuntimeError(
            "Create QApplication before calling create_waveform()."
        )

    screen = app.primaryScreen()
    if screen is None:
        raise RuntimeError("No primary display is available.")

    _overlay = AnimationWindow()
    _overlay.fit_to_screen(screen.geometry())
    screen.geometryChanged.connect(_overlay.fit_to_screen)

    _overlay.show()
    _overlay.raise_()

    # The native handle is reliable after show(). Apply the Windows style on
    # the next event-loop pass, then begin the low-frequency taskbar check.
    QTimer.singleShot(0, _overlay.start_taskbar_monitor)

    return _overlay


def hide_waveform() -> None:
    """Hide the shared waveform overlay until the next wake word."""
    if _overlay is not None:
        _overlay.hide()


def display_amplitude(amplitude: float) -> None:
    """Send an amplitude sample to the shared overlay."""
    if _overlay is None:
        create_waveform()

    assert _overlay is not None
    _overlay.display_amplitude(amplitude)
