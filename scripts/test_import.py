import traceback
try:
    import f5_tts.infer.infer_cli
    print('OK')
except Exception as e:
    traceback.print_exc()
