"""Compatibility entry point for the packaged SenseVoice recognizer."""

from _package_bootstrap import alias_module, ensure_src_path

if __name__ == "__main__":
    ensure_src_path()
    from zhuangfy.speech.sensevoice import main

    main()
else:
    alias_module(__name__, "zhuangfy.speech.sensevoice")
