# Vexa wake-word model

Place a custom openWakeWord ONNX model named `vexa.onnx` in this directory.
The model path and activation threshold can instead be configured in `.env`:

```dotenv
VEXA_WAKE_WORD_MODEL=C:/path/to/vexa.onnx
VEXA_WAKE_WORD_THRESHOLD=0.5
```

Train the phrase **hey Vexa** with openWakeWord's custom-model notebook:
https://github.com/dscripka/openWakeWord/blob/main/notebooks/automatic_model_training.ipynb
