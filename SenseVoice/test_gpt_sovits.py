import json
import tempfile
import unittest
from pathlib import Path

from gpt_sovits import SpeechSettings, available_models, load_settings, save_model_config, save_settings


class SpeechSettingsTests(unittest.TestCase):
    def test_requires_local_service_and_reference_before_switching(self):
        with tempfile.TemporaryDirectory() as directory:
            sample = Path(directory) / "reference.wav"
            sample.write_bytes(b"RIFFtest")
            with self.assertRaisesRegex(ValueError, "本机"):
                SpeechSettings("gpt_sovits", "https://example.com", str(sample), "你好").validated()
            with self.assertRaisesRegex(ValueError, "准确台词"):
                SpeechSettings("gpt_sovits", reference_audio=str(sample)).validated()
            settings = SpeechSettings("gpt_sovits", reference_audio=str(sample), reference_text="你好")
            payload = settings.payload("欢迎回来。")
            self.assertEqual(payload["ref_audio_path"], str(sample))
            self.assertEqual(payload["text"], "欢迎回来。")
            self.assertFalse(payload["streaming_mode"])
            path = Path(directory) / "tts_settings.json"
            save_settings(settings, path)
            self.assertEqual(load_settings(path), settings)

    def test_default_keeps_system_voice_without_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load_settings(Path(directory) / "missing.json").engine, "system")

    def test_discovers_latest_paired_weights_and_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pretrained = root / "GPT_SoVITS" / "pretrained_models"
            (pretrained / "v2Pro").mkdir(parents=True)
            (pretrained / "s1v3.ckpt").write_bytes(b"base")
            (pretrained / "v2Pro" / "s2Gv2Pro.pth").write_bytes(b"base")
            gpt = root / "GPT_weights_v2Pro"
            sovits = root / "SoVITS_weights_v2Pro"
            gpt.mkdir()
            sovits.mkdir()
            for epoch in (2, 15):
                (gpt / f"zhuangfangyi_v2pro-e{epoch}.ckpt").write_bytes(b"gpt")
            (sovits / "zhuangfangyi_v2pro_e8_s912.pth").write_bytes(b"sovits")
            voice = root / "prepared"
            (voice / "wav").mkdir(parents=True)
            (voice / "wav" / "chr_0030_zhuangfy_chrbark_weap_01.wav").write_bytes(b"RIFF")
            (voice / "manifest.csv").write_text(
                "filename,transcript\nchr_0030_zhuangfy_chrbark_weap_01.mp3,测试台词\n", encoding="utf-8-sig")
            models = available_models(root, voice)
            self.assertEqual([model.model_id for model in models], ["v2pro_base", "zhuangfangyi_v2pro"])
            trained = models[1]
            self.assertEqual(trained.gpt_weight.name, "zhuangfangyi_v2pro-e15.ckpt")
            self.assertEqual(trained.reference_text, "测试台词")
            config = root / "shimano.yaml"
            config.write_text("custom:\n  t2s_weights_path: old\n  vits_weights_path: old\n"
                              "v1:\n  t2s_weights_path: leave-alone\n", encoding="utf-8")
            save_model_config(trained, config)
            result = config.read_text(encoding="utf-8")
            self.assertIn("zhuangfangyi_v2pro-e15.ckpt", result)
            self.assertIn("leave-alone", result)
            self.assertIn(json.dumps(str(gpt / "zhuangfangyi_v2pro-e15.ckpt")), result)
