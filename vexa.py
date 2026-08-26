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
import json
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from waveform_overlay import (
    create_waveform,
    display_amplitude,
    hide_waveform,
    initialize_waveform,
    shutdown_application,
)
import tool_types
import vexa_tools

FUNCTIONS = {
    "open_application": vexa_tools.open_application
}



END_LISTENING_KEYWORD = "stop listening"
END_CONVERSATION_KEYWORD = "end conversation"
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

    def restart(self):
        if self.stream.active:
            self.stream.abort()  # Discard queued microphone data
        self.stream.start()


def clear_buffer():
    while msvcrt.kbhit():
        msvcrt.getch()

def wake_word_match(transcript):
    current = transcript.lower().strip()

    previous = getattr(
        wake_word_match,
        "previous_sample",
        ""
    )

    combined = f"{previous} {current}".strip()

    current_score = ratio(current, "hey vexa")
    combined_score = ratio(combined, "hey vexa")

    matched = max(current_score, combined_score) >= 75

    if matched:
        wake_word_match.previous_sample = ""
    else:
        wake_word_match.previous_sample = current

    return matched

def record_fixed_window(
    microphone,
    seconds=1.5,
    filename="wake_word.wav",
):
    frames = int(seconds * SAMPLE_RATE)
    audio = microphone.read(frames)
    sf.write(filename, audio, SAMPLE_RATE)

    # if the RMS amplitude is below a threshold, consider it silence and return False
    rms = np.sqrt(
        np.mean((audio.astype(np.float32) / 32768.0) ** 2)
    )
    return bool(rms >= 0.005)


def record_audio(
    app: bool = False,
    seconds: int = 5,
    filename: str = AUDIO_FILE,
    max_seconds: int = 30,
    silence_threshold: float = 0.005,
    silence_duration: float = 2.0,
    microphone: MicrophoneStream | None = None,
):
    
    # Short chunks make the waveform react promptly while the user speaks.
    chunk_size = int(0.05 * SAMPLE_RATE)
    chunks = []
    elapsed = 0.0
    silent_chunks = 0
    has_heard_speech = False
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

        if amplitude >= silence_threshold:
            silent_chunks = 0
            has_heard_speech = True
        elif has_heard_speech:
            silent_chunks += 1

        required_silent_chunks = int(
            silence_duration / chunk_duration
        )
        if silent_chunks >= required_silent_chunks:
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
                "Listen for the wake phrase 'Vexa'. "
                "Vexa is pronounced 'VEX-uh'. "
                "The recording may contain only this short phrase."  
            ),
            
            temperature=0,
            extra_body={
                "keywords": [
                    "power down",
                    "vexa",
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
    listening = False
    microphone = MicrophoneStream()
    microphone.start()
    conversation = [{
        "role": "system",
        "content": "You are Vexa, a helpful desktop AI assistant. "
        "Be clear, concise, and practical. Respond in 3 sentences or less, "
        "unless told otherwise"
        "User is talking to you through a microphone and the users words are being transcribed to you."
        "If user says 'stop listening' respond with 'No longer listening'"
        "If user says 'end conversation' respond with 'Conversation ended'"
    }]
    

    try:
        print("------Vexa Online------\n")
        
        while True:
            start = time.time()

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
                listening = True
                pygame.mixer.music.load("listening.mp3")
                pygame.mixer.music.play()
                print("\nVexa: Listening\n")

                time.sleep(0.5)  # Debounce to prevent rapid toggling
                
            # Listen for wake word if no input mode is active
            elif listening:
                try:
                
                    audio_detected = record_fixed_window(
                        microphone,
                        seconds=1.5,
                        filename="wake_word.wav",
                    )
                    if not audio_detected:
                        print("No audio detected")
                        continue
                    else:
                        print("Audio detected")
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

                        convo_ended = False
                        while not convo_ended:
                            microphone.restart()
                            record_audio(
                                app=True,
                                seconds=30,
                                microphone=microphone,
                            )
                            user_input = transcribe_audio()

                            print("TRANSCRIPT:", repr(user_input))

                            if END_LOOP_KEYWORD in user_input.lower():
                                print("\nVexa: Powering down.\n")
                                listening = False
                                convo_ended = True
                                return

                            if END_CONVERSATION_KEYWORD in user_input.lower():
                                convo_ended = True
                                print("\nVexa: Conversation ended.\n")

                            if END_LISTENING_KEYWORD in user_input.lower():
                                listening = False
                                convo_ended = True
                                print("\nVexa: Stopped listening.\n")
                                

                            if user_input.lower() == "clear memory":
                                conversation = [conversation[0]]
                                print("\nVexa: Memory cleared.\n")
                                continue

                            print(f"You said: {user_input}\n")

                            if not user_input.strip().strip(". "):
                                continue

                            conversation.append({
                                "role": "user",
                                "content": user_input
                            })

                        

                            response = client.responses.create(
                            model="gpt-5.4",
                            input = conversation,
                            tools= tool_types.tools
                            )
                            for item in response.output:
                                if item.type == "function_call":

                                    function_name = item.name
                                    arguments = json.loads(item.arguments)

                                    print(f"Calling: {function_name}")
                                    print(f"Arguments: {arguments}")

                                    function = FUNCTIONS.get(function_name)

                                    if function:
                                        result = function(**arguments)
                            reply = response.output_text
                            
                            conversation.append({
                                "role": "assistant",
                                "content": reply
                            })
                            conversation = [conversation[0]] + conversation[-MAX_MESSAGES:]

                            

                            try:
                                if reply:
                                    print("\nVexa: ",reply,"\n")
                                    speak_text(reply)
                            except Exception as e:
                                print(f"Vexa: TTS error: {e}")
                            finally:
                                hide_waveform() 
                            elapsed = time.time() - start
                            time.sleep(max(0, (1/10) - elapsed))
                            

                            while pygame.mixer.music.get_busy():
                                time.sleep(0.1)

                            if not convo_ended:
                                create_waveform()
                                pygame.mixer.music.load("listening.mp3")
                                pygame.mixer.music.play()
                except Exception as error:
                    print(f"Wake word check failed: {error}")

            if not listening:
                time.sleep(0.05)
                
                

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
