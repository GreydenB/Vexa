from itertools import cycle

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from voice_animation import create_waveform, display_amplitude



app = QApplication([])
create_waveform()

amplitudes = cycle([0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 1, 0.90, 0.80, 0.70, 0.60, 0.50])

sample_timer = QTimer()
sample_timer.timeout.connect(
    lambda: display_amplitude(next(amplitudes))
)
sample_timer.start(40)  # New amplitude every 40 milliseconds

app.exec()
