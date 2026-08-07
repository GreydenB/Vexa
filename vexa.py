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
import string
import threading


from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from voice_animation import create_waveform, display_amplitude, hide_waveform





END_LOOP_KEYWORD = "power down"
MAX_MESSAGES = 10
SAMPLE_RATE = 16000
AUDIO_FILE = "user_input.wav"
TTS_FILE = "vexa_reply.mp3"
WAKE_WORD = "hey vexa"


#load .env vars
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
pygame.mixer.init()

def clear_buffer():
    while msvcrt.kbhit():
        msvcrt.getch()

def listen_for_wake_word(seconds: int = 3) -> bool:
    try:
        record_audio(False,seconds=seconds, filename="wake_word.wav")
        translator = str.maketrans('', '', string.punctuation)
        transcript = transcribe_audio("wake_word.wav").lower().translate(translator)
        print(f"Wake word check transcript: {transcript}")

        if WAKE_WORD in transcript:
            return True
        
    except Exception as e:
        print(f"Wake word check failed: {e}")
        return False


def record_audio(
    app: bool = False,
    seconds: int = 5,
    filename: str = AUDIO_FILE,
    max_seconds: int = 30,
    silence_threshold: float = 0.02,
    silence_duration: float = 2.5,
):
    
    chunk_size = int(0.2 * SAMPLE_RATE)
    chunks = []
    elapsed = 0.0
    silent_chunks = 0

    while elapsed < max_seconds:
        chunk = sd.rec(
            frames=chunk_size,
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
        )
        sd.wait()

        chunks.append(chunk)
        chunk_duration = len(chunk) / SAMPLE_RATE
        elapsed += chunk_duration

        amplitude = np.abs(chunk).mean() / 32768.0
        if (app):
            display_amplitude(amplitude)

        if amplitude < silence_threshold:
            silent_chunks += 1
        else:
            silent_chunks = 0

        if elapsed >= seconds and silent_chunks >= max(2.5, int(silence_duration / chunk_duration)):
            break

    if not chunks:
        return

    recording = np.concatenate(chunks, axis=0)
    sf.write(filename, recording, SAMPLE_RATE)
    #print(f"Recording Ended ({round(elapsed, 2)}s):")
    
def transcribe_audio(filename: str = AUDIO_FILE) -> str:
    with open(filename, "rb") as audio_file:
        transcript = client.audio.transcriptions.create(
            model="gpt-transcribe",
            file=audio_file,
            language=["en"],
            prompt="The speaker is giving commands to a desktop AI assistant.",
            keywords=[
                "OpenAI",
                "PySide6",
                "Python",
                "Vexa",
                "Power Down"
            ],
        )
    if "openai pyside6 python vexa" in transcript.text:
        transcript.text = None
        
    return transcript.text

def speak_text(text: str, filename: str = TTS_FILE):
    with client.audio.speech.with_streaming_response.create(
        model="gpt-4o-mini-tts",
        voice="nova",
        input=text
    ) as response:
        response.stream_to_file(filename)

    pygame.mixer.music.load(filename)
    pygame.mixer.music.play()

    while pygame.mixer.music.get_busy():
        time.sleep(0.1)
    pygame.mixer.music.unload() 
    # unlocks file so bot an rewrite it
    
print("------Vexa Online------\n")

def main():
    wake_word_detected = False
    conversation = [{
        "role": "system",
        "content": "You are Vexa, a helpful desktop AI assistant. "
        "Be clear, concise, and practical. Respond in 3 sentences or less, "
        "unless told otherwise"
    }]

    # Ensure a single QApplication exists and its event loop is running so
    # the waveform overlay can be created on demand. The event loop is
    # started in a daemon thread so the existing console loop can continue.
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
        threading.Thread(target=app.exec, daemon=True).start()




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

            try:
                record_audio(True)
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
        elif listen_for_wake_word():
            wake_word_detected = True
            pygame.mixer.music.load("listening.mp3")
            pygame.mixer.music.play()
            # Show the waveform overlay (QApplication is already running).
            create_waveform()
            print("Wake word detected. Listening for your command...")
            try:
                record_audio(True)
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

main()
