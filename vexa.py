from email.mime import text

from openai import OpenAI
from dotenv import load_dotenv
import os
import sounddevice as sd
import soundfile as sf
import numpy as np
import keyboard
import pygame
import time
import msvcrt
import threading
from rapidfuzz.fuzz import ratio

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from waveform_overlay import (
    create_waveform,
    display_amplitude,
    hide_waveform,
    initialize_waveform,
    shutdown_application,
)

END_LOOP_KEYWORD = "power down"
MAX_MESSAGES = 10
SAMPLE_RATE = 16000
AUDIO_FILE = "user_input.wav"
TTS_FILE = "vexa_reply.mp3"

#load .env vars
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
pygame.mixer.init()

#
class MicrophoneStream:
    def __init__(
        self,
        sample_rate: int = SAMPLE_RATE,
        block_duration: float = 0.1,
    ):
        self.sample_rate = sample_rate
        self.block_size = int(sample_rate * block_duration)

        self.stream = sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
            blocksize=self.block_size,
        )

    def start(self):
        self.stream.start()

    def read(self, frames=None):
        frames = frames or self.block_size
        audio, overflowed = self.stream.read(frames)

        if overflowed:
            print("Warning: microphone input overflow")

        return audio.copy()

    def close(self):
        if self.stream.active:
            self.stream.stop()
        self.stream.close()



def clear_buffer():
    while msvcrt.kbhit():
        msvcrt.getch()

def wake_word_match(transcript):
    score = ratio(transcript, "hey vexa")
    print("Wake score:", score)

    return score >= 75

def record_fixed_window(
    microphone,
    seconds=1.5,
    filename="wake_word.wav",
):
    frames = int(seconds * SAMPLE_RATE)
    audio = microphone.read(frames)
    sf.write(filename, audio, SAMPLE_RATE)

def record_audio(
    app: bool = False,
    seconds: int = 5,
    filename: str = AUDIO_FILE,
    max_seconds: int = 30,
    silence_threshold: float = 0.02,
    silence_duration: float = 2.5,
    microphone: MicrophoneStream | None = None,
):
    
    # Short chunks make the waveform react promptly while the user speaks.
    chunk_size = int(0.05 * SAMPLE_RATE)
    chunks = []
    elapsed = 0.0
    silent_chunks = 0

    while elapsed < max_seconds:
        try:
            chunk = microphone.read(chunk_size)
        except Exception as e:
            print(f"Error reading microphone: {e}")
            break

        chunks.append(chunk)

        chunk_duration = len(chunk) / SAMPLE_RATE
        elapsed += chunk_duration

        samples = chunk.astype(np.float32) / 32768.0
        amplitude = float(np.sqrt(np.mean(samples * samples)))

        # Convert microphone RMS to the overlay's expected 0-1 range.
        # Roughly -50 dB is quiet and -15 dB is loud speech.
        decibels = 20.0 * np.log10(max(amplitude, 1e-7))
        visual_amplitude = float(
            np.clip((decibels + 50.0) / 35.0, 0.0, 1.0)
        )
        if app:
            display_amplitude(visual_amplitude)

        if amplitude < silence_threshold:
            silent_chunks += 1
        else:
            silent_chunks = 0

        required_silent_chunks = int(
            silence_duration / chunk_duration
        )
        if elapsed >= seconds and silent_chunks >= required_silent_chunks:
            break

    if chunks:
        recording = np.concatenate(chunks, axis=0)
        sf.write(filename, recording, SAMPLE_RATE)
    
    
def transcribe_audio(filename: str = AUDIO_FILE) -> str:
    with open(filename, "rb") as audio_file:
        transcript = client.audio.transcriptions.create(
            model="gpt-transcribe",
            file=audio_file,
            prompt=(
                "The speaker is giving a command to a desktop AI assistant named Vexa."
                "Listen for the wake phrase 'Hey Vexa'. "
                "Vexa is pronounced 'VEX-uh'. "
                "The recording may contain only this short phrase."  
            ),
            
            temperature=0,
            extra_body={
                "keywords": [
                    "power down",
                    "vexa",
                    "hey vexa",
                ],
                "languages": ["en"],
            },
        )

    return transcript.text.strip()

def speak_text(text: str, filename: str = TTS_FILE):
    with client.audio.speech.with_streaming_response.create(
        model="gpt-4o-mini-tts",
        voice="nova",
        input=text
    ) as response:
        response.stream_to_file(filename)

    audio, rate = sf.read(filename, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)

    chunk = rate // 20  # One volume value every 50 ms
    volumes = [
        min(1.0, float(np.sqrt(np.mean(audio[i:i + chunk] ** 2))) * 5)
        for i in range(0, len(audio), chunk)
    ]

    pygame.mixer.music.load(filename)
    pygame.mixer.music.play()

    while pygame.mixer.music.get_busy():
        position = max(0, pygame.mixer.music.get_pos() // 50)
        display_amplitude(volumes[min(position, len(volumes) - 1)])
        time.sleep(0.1)

    display_amplitude(0.0)
    pygame.mixer.music.unload() 


def main():
    microphone = MicrophoneStream()
    microphone.start()
    conversation = [{
        "role": "system",
        "content": "You are Vexa, a helpful desktop AI assistant. "
        "Be clear, concise, and practical. Respond in 3 sentences or less, "
        "unless told otherwise"
    }]

    try:
        print("------Vexa Online------\n")
        while True:
            start = time.time()
            has_user_input = False

            # Swap to text input mode if "t+=" is pressed
            if keyboard.is_pressed("t+="):
                clear_buffer()
                user_input = input("Greyden: ").strip()
                has_user_input = True

                if END_LOOP_KEYWORD in user_input.lower():
                    break

                if user_input.lower() == "clear memory":
                    conversation = [conversation[0]]
                    print("\nVexa: Memory cleared.\n")
                    continue

            # Swap to audio input mode if "shift+q" is pressed
            if keyboard.is_pressed("shift+q"):

                print("Recording Started:")
                create_waveform()

                try:
                    record_audio(True, microphone)
                    user_input = transcribe_audio()
                    has_user_input = True

                    if END_LOOP_KEYWORD in user_input.lower():
                        break

                    elif user_input.lower() == "clear memory":
                        conversation = [conversation[0]]
                        print("\nVexa: Memory cleared.\n")
                        continue

                    print(f"You said: {user_input}\n")

                except Exception as e:
                    print(f"\nVexa: Audio error: {e}\n")
                    continue

            # Listen for wake word if no input mode is active
            else:
                try:
                
                    record_fixed_window(
                        microphone,
                        seconds=1.5,
                        filename="wake_word.wav",
                    )

                    transcript = transcribe_audio(
                        "wake_word.wav"
                    ).lower()

                    print(
                        f"Wake word check transcript: {transcript}"
                    )

                    if wake_word_match(transcript):
                        pygame.mixer.music.load("listening.mp3")
                        pygame.mixer.music.play()
                        create_waveform()

                        print(
                            "Wake word detected. "
                            "Listening for your command..."
                        )

                        record_audio(
                            app=True,
                            microphone=microphone,
                        )
                        user_input = transcribe_audio()
                        has_user_input = True

                        if END_LOOP_KEYWORD in user_input.lower():
                            break

                        if user_input.lower() == "clear memory":
                            conversation = [conversation[0]]
                            print("\nVexa: Memory cleared.\n")
                            continue

                        print(f"You said: {user_input}\n")

                except Exception as error:
                    print(f"Wake word check failed: {error}")

            if has_user_input:
                if not user_input.strip().strip(". "):
                    continue

                conversation.append({
                    "role": "user",
                    "content": user_input
                })

            

                response = client.responses.create(
                model="gpt-5.4",
                input = conversation
                )

                reply = response.output_text

                conversation.append({
                    "role": "assistant",
                    "content": reply
                })
                conversation = [conversation[0]] + conversation[-MAX_MESSAGES:]

                print("\nVexa: ",reply,"\n")

                try:
                    speak_text(reply)
                except Exception as e:
                    print(f"Vexa: TTS error: {e}")
                finally:
                    hide_waveform() 
                
            elapsed = time.time() - start
            time.sleep(max(0, (1/10) - elapsed))

    finally:
        microphone.close()


if __name__ == "__main__":
    app = QApplication([])
    initialize_waveform()

    def run_assistant() -> None:
        try:
            main()
        finally:
            shutdown_application()

    worker = threading.Thread(target=run_assistant, daemon=True)
    worker.start()

    app.exec()
