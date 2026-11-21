import copy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import clip_evidence
import clipping
import face_track
import shorts


class EditListTests(unittest.TestCase):
    def setUp(self):
        self.media = {'duration': 20, 'width': 1280, 'height': 720}
        self.manifest = {'clips': [{'id': 'one', 'segments': [{'start': 1, 'end': 5}], 'layout': 'fit'}]}

    def test_rejects_invalid_numbers_and_ranges(self):
        for value in (float('nan'), float('inf'), -1, True, '2'):
            manifest = copy.deepcopy(self.manifest)
            manifest['clips'][0]['segments'][0]['start'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                clipping.validate_manifest(manifest, self.media)
        self.manifest['clips'][0]['segments'][0]['end'] = 21
        with self.assertRaises(ValueError):
            clipping.validate_manifest(self.manifest, self.media)

    def test_rejects_unsafe_ids_and_outside_webcam_crop(self):
        self.manifest['clips'][0]['id'] = '../escape'
        with self.assertRaises(ValueError):
            clipping.validate_manifest(self.manifest, self.media)
        self.manifest['clips'][0].update(id='one', layout='webcam_stack', webcam_box=[1200, 0, 200, 200])
        with self.assertRaises(ValueError):
            clipping.validate_manifest(self.manifest, self.media)

    def test_virtual_captions_follow_multi_part_edit(self):
        from recut import virtual_transcript
        transcript = {'language': 'ar', 'segments': [{'words': [
            {'word': ' أول', 'start': 2, 'end': 3},
            {'word': ' تاني', 'start': 10, 'end': 11}]}]}
        remapped = virtual_transcript(transcript, [{'start': 1, 'end': 4}, {'start': 9, 'end': 12}])
        self.assertEqual(remapped['segments'][0]['words'][0]['start'], 1)
        self.assertEqual(remapped['segments'][1]['words'][0]['start'], 4)
        self.assertEqual(remapped['segments'][1]['words'][0]['word'], ' تاني')

    def test_arabic_caption_text_retained_and_ass_injection_escaped(self):
        with tempfile.TemporaryDirectory(dir=clipping.ROOT/'temp') as directory:
            root = Path(directory)
            srt = root/'captions.srt'
            srt.write_text('1\n00:00:00,000 --> 00:00:02,000\nبص العربية دي وصلت GTA 6\n', encoding='utf-8')
            clipping.make_ass(srt, root/'captions.ass', 1080, 1920, '{\\pos(0,0)}')
            text = (root/'captions.ass').read_text(encoding='utf-8-sig')
            self.assertIn('بص العربية دي وصلت GTA 6', text)
            self.assertIn('\\{', text)

    def test_orphaned_final_word_merges_without_changing_source_words(self):
        with tempfile.TemporaryDirectory(dir=clipping.ROOT/'temp') as directory:
            path = Path(directory)/'captions.srt'
            path.write_text('1\n00:00:07,800 --> 00:00:09,900\nwhat you can do for your\n\n2\n00:00:09,900 --> 00:00:10,279\ncountry.\n', encoding='utf-8')
            clipping.smooth_short_captions(path)
            text = path.read_text(encoding='utf-8')
            self.assertIn('what you can do for your country.',text)
            self.assertIn('00:00:07,800 --> 00:00:10,279',text)
            self.assertEqual(text.count('-->'),1)

    def test_delayed_audio_keeps_leading_silence_in_video_timeline(self):
        import numpy as np
        with tempfile.TemporaryDirectory(dir=clipping.ROOT/'temp') as directory:
            job = Path(directory)
            source = job/'delayed.mkv'
            clipping.run(['ffmpeg','-hide_banner','-loglevel','error','-y',
                '-f','lavfi','-i','color=c=black:s=160x90:r=10:d=3',
                '-itsoffset','1','-f','lavfi','-i','sine=frequency=440:sample_rate=16000:duration=1',
                '-c:v','libx264','-c:a','pcm_s16le',source])
            pcm = np.fromfile(clipping.decode_pcm(job,source),dtype='float32')
            self.assertGreaterEqual(len(pcm),31000)
            self.assertLess(float(np.abs(pcm[:14000]).max()),0.0001)
            self.assertGreater(float(np.abs(pcm[17600:24000]).max()),0.05)

    def test_render_source_without_audio_or_captions(self):
        with tempfile.TemporaryDirectory(prefix='test-silent-', dir=clipping.ROOT/'jobs') as directory:
            job = Path(directory)
            source = job / 'silent.mp4'
            clipping.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=size=320x180:duration=1.5',
                          '-an', '-c:v', 'libx264', source])
            media = clipping.probe(source)
            clipping.dump(job/'job.json', {'source':str(source), 'media':media})
            clipping.dump(job/'clips.json', {'clips':[{'id':'silent', 'segments':[{'start':0,'end':1}], 'layout':'fit'}]})
            clipping.render(SimpleNamespace(job=str(job), manifest=None, width=360, force=True))
            final = clipping.probe(job/'exports/silent/final.mp4')
            self.assertFalse(final['has_audio'])
            self.assertEqual((final['width'], final['height']), (360,640))
            self.assertEqual((job/'exports/silent/captions.srt').read_text(), '')
            exports_before = (job / 'exports.json').read_bytes()
            clipping.render(SimpleNamespace(job=str(job), manifest=None, width=360, force=False, preview=True))
            self.assertTrue((job / 'previews/highlights/review.html').is_file())
            self.assertEqual((job / 'exports.json').read_bytes(), exports_before)


class NarratedShortTests(unittest.TestCase):
    def test_caption_splits_by_width_and_preserves_words_at_both_resolutions(self):
        texts = ["دي", "W" * 50, "عربية", "GTA"]
        beat = {"end_frame": 90, "overlay": None, "words": [
            {"text": text, "start": i * .5, "end": (i + 1) * .5}
            for i, text in enumerate(texts)]}
        with tempfile.TemporaryDirectory(dir=clipping.ROOT / 'temp') as directory:
            output = Path(directory)
            shorts.captions([beat], "أنهي واحدة؟", output, 1080, 1920)
            srt = (output / "captions.srt").read_text(encoding="utf-8")
            ass = (output / "captions.ass").read_text(encoding="utf-8-sig")
            self.assertIn("\n" + texts[1] + "\n", srt)
            self.assertIn(r"{\fs", ass)
            shorts.captions([beat], "أنهي واحدة؟", output, 360, 640)
            self.assertEqual((output / "captions.srt").read_text(encoding="utf-8"), srt)

    def test_overlay_appears_on_first_frame_after_word_onset(self):
        beat = {"end_frame": 90, "words": [
            {"text": "دي", "start": 1.0, "end": 1.11},
            {"text": "GTA", "start": 1.111, "end": 1.4},
            {"text": "ستة", "start": 1.4, "end": 1.7}],
            "overlay": {"text": "6", "style": "answer_wrong", "word": 2}}
        with tempfile.TemporaryDirectory(dir=clipping.ROOT / 'temp') as directory:
            output = Path(directory)
            shorts.captions([beat], "أنهي واحدة؟", output, 1080, 1920)
            ass = (output / "captions.ass").read_text(encoding="utf-8-sig")
            # At 30fps, frame 34 is the first frame after 1.111s. The ASS
            # centisecond must fall after frame 33 and at or before frame 34.
            self.assertIn("Dialogue: 1,0:00:01.13,0:00:02.63,answer_wrong", ass)
            self.assertIn("دي GTA ستة", (output / "captions.srt").read_text(encoding="utf-8"))


class EvidenceTests(unittest.TestCase):
    def test_search_reuses_notes_and_speech_and_ignores_stale_visual_notes(self):
        with tempfile.TemporaryDirectory(dir=clipping.ROOT / "temp") as directory:
            job = Path(directory).resolve()
            source = job / "video.mp4"
            source.write_bytes(b"sample")
            (job / "shots").mkdir()
            (job / "shots/scene.jpg").touch()
            note = clip_evidence.visual_note(job, 10, 2, 4, "shots/scene.jpg", "عَرَبيّة GTA في الشارع")
            clip_evidence.save_note(job, source, note)
            clip_evidence.save_note(job, source, note)
            (job / "transcript.txt").write_text("[5.000 --> 6.000] GTA عربية جديدة\n", encoding="utf-8")
            matches = clip_evidence.search(job, source, 10, "عربية GTA", 5)
            self.assertEqual([match["kind"] for match in matches], ["visual", "speech"])
            self.assertEqual(matches[0]["frame"], "shots/scene.jpg")
            with self.assertRaises(ValueError):
                clip_evidence.visual_note(job, 10, 2, 4, "../scene.jpg", "Invalid")
            source.write_bytes(b"replacement")
            self.assertEqual(clip_evidence.search(job, source, 10, "عربية GTA", 5)[0]["kind"], "speech")


class FaceTrackAndUpgradeTests(unittest.TestCase):
    def test_face_track_smoothing_cut_snap_and_ffmpeg_pan_expression(self):
        cw, ch = face_track.compute_crop_size(1920, 1080, 1080, 1920)
        self.assertEqual((cw % 2, ch % 2), (0, 0))
        self.assertLessEqual(cw, 1920)
        self.assertEqual(ch, 1080)

        # Steady jitter within deadband should stay rock solid, then snap on hard cut.
        samples = [
            (0.0, 100.0, 0.0),
            (0.5, 108.0, 2.0),   # within deadband
            (1.0, 96.0, 2.0),    # within deadband
            (1.5, 900.0, 45.0),  # hard cut -> snap immediately
            (2.0, 905.0, 1.0),
        ]
        smoothed = face_track.smooth_positions(samples, default_x=500.0, max_x=1000.0)
        self.assertEqual(smoothed[1][1], 100.0)
        self.assertEqual(smoothed[3][1], 900.0)

        kfs = face_track.compress_keyframes(smoothed)
        mode, flt = face_track.build_crop_filter(180, 320, 0, 140, kfs, 180, 320)
        self.assertEqual(mode, "tracked_pan")
        self.assertIn("clip(", flt)

        # Execute the generated pan filter in real FFmpeg on a 2s 320x320 synthetic video.
        with tempfile.TemporaryDirectory(dir=clipping.ROOT / "temp") as directory:
            out = Path(directory) / "pan.mp4"
            clipping.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc=size=320x320:rate=15:duration=2",
                "-vf", flt, "-c:v", "libx264", "-preset", "ultrafast", out,
            ])
            info = clipping.probe(out)
            self.assertEqual((info["width"], info["height"]), (180, 320))

    def test_caption_word_highlight_arabic_font_and_validation(self):
        media = {"duration": 20, "width": 1280, "height": 720}
        good = {"clips": [{
            "id": "hi-1",
            "segments": [{"start": 0, "end": 4}],
            "layout": "face_track",
            "caption_options": {"style": "word_highlight", "font": "Tajawal", "highlight_color": "cyan"},
        }]}
        validated = clipping.validate_manifest(good, media)
        self.assertEqual(validated[0]["layout"], "face_track")

        for bad_opts in (
            {"font": "Arial},injection"},
            {"style": "karaoke"},
            {"highlight_color": "red"},
        ):
            bad = copy.deepcopy(good)
            bad["clips"][0]["caption_options"] = bad_opts
            with self.subTest(bad_opts=bad_opts), self.assertRaises(ValueError):
                clipping.validate_manifest(bad, media)

        with tempfile.TemporaryDirectory(dir=clipping.ROOT / "temp") as directory:
            root = Path(directory)
            srt = root / "captions.srt"
            srt.write_text("", encoding="utf-8")
            virtual = {"segments": [{"words": [
                {"word": " بص", "start": 0.1, "end": 0.4},
                {"word": " اللقطة", "start": 0.45, "end": 0.9},
                {"word": " {GTA}", "start": 0.95, "end": 1.4},
            ]}]}
            ass = root / "captions.ass"
            clipping.make_ass(
                srt, ass, 360, 640, "أقوى لقطة",
                font="Tajawal", style="word_highlight", highlight_color="cyan", virtual=virtual,
            )
            text = ass.read_text(encoding="utf-8-sig")
            self.assertIn("Style: Caption,Tajawal,", text)
            self.assertIn(r"{\1c&H00FFFF00&}", text)
            self.assertIn(r"\{GTA\}", text)

            # Verify FFmpeg renders bundled Tajawal font via fontsdir cleanly.
            out = root / "font-check.mp4"
            ass_vf = f"ass=filename=captions.ass:fontsdir='{clipping.ffmpeg_filter_path(clipping.FONTS_DIR)}'"
            clipping.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=0x10131b:s=360x640:r=15:d=1.5",
                "-vf", ass_vf, "-c:v", "libx264", "-preset", "ultrafast", out.name,
            ], cwd=root)
            self.assertEqual((clipping.probe(out)["width"], clipping.probe(out)["height"]), (360, 640))

    def test_asr_prompt_resolution_and_validation(self):
        self.assertEqual(
            clipping.resolve_asr_prompt(SimpleNamespace(language="ar")),
            clipping.DEFAULT_ARABIC_PROMPT,
        )
        self.assertEqual(
            clipping.resolve_asr_prompt(SimpleNamespace(language="ar", no_prompt=True)),
            "",
        )
        self.assertEqual(
            clipping.resolve_asr_prompt(SimpleNamespace(language="en")),
            "",
        )
        self.assertIn(
            "Podcast",
            clipping.resolve_asr_prompt(SimpleNamespace(language="ar", profile="arabic-podcast")),
        )
        self.assertEqual(
            clipping.resolve_asr_prompt(SimpleNamespace(language="ar", prompt="GTA 6, Rockstar")),
            "GTA 6, Rockstar",
        )
        with self.assertRaises(ValueError):
            clipping.resolve_asr_prompt(SimpleNamespace(language="ar", prompt="bad\nmultiline"))

    def test_render_face_track_fallback_records_receipt_metadata(self):
        with tempfile.TemporaryDirectory(prefix="test-ft-", dir=clipping.ROOT / "jobs") as directory:
            job = Path(directory)
            source = job / "src.mp4"
            clipping.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc=size=640x360:rate=15:duration=1.5",
                "-c:v", "libx264", "-preset", "ultrafast", source,
            ])
            media = clipping.probe(source)
            clipping.dump(job / "job.json", {"source": str(source), "media": media})
            clipping.dump(job / "clips.json", {"clips": [{
                "id": "ft1",
                "segments": [{"start": 0, "end": 1.2}],
                "layout": "face_track",
                "caption_options": {"style": "word_highlight", "font": "Tajawal"},
            }]})
            clipping.render(SimpleNamespace(job=str(job), manifest=None, width=360, force=True))
            receipt = clipping.read(job / "exports/ft1/receipt.json")
            self.assertEqual((receipt["width"], receipt["height"]), (360, 640))
            self.assertEqual(receipt["caption_font"], "Tajawal")
            self.assertEqual(receipt["caption_style"], "word_highlight")
            self.assertEqual(receipt["face_track"]["mode"], "fallback_center")



class PluginInstallTests(unittest.TestCase):
    def test_first_install_and_update_preserve_host_config_and_previous_files(self):
        import tomllib
        from tools.install_plugin import install

        with tempfile.TemporaryDirectory(dir=clipping.ROOT / "temp") as directory:
            profile = Path(directory)
            install("Both", profile)
            settings = profile / ".ai-clipping"
            self.assertEqual(Path(json.loads((settings / "config.json").read_text())["root"]), clipping.ROOT)
            config_path = profile / ".codex/config.toml"
            with config_path.open("a", encoding="utf-8") as stream:
                stream.write('\n[unrelated]\nvalue = 42\n')
            config_before = config_path.read_bytes()
            installed = profile / ".gemini/config/plugins/video-clipper/README.md"
            installed.write_text("previous revision", encoding="utf-8")
            install("Both", profile)
            backups = list((settings / "backups/antigravity").iterdir())
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / "README.md").read_text(), "previous revision")
            self.assertEqual(installed.read_bytes(), (clipping.ROOT / "plugin/README.md").read_bytes())
            install("Both", profile)
            self.assertEqual(len(list((settings / "backups/antigravity").iterdir())), 1)
            self.assertEqual(config_path.read_bytes(), config_before)
            self.assertTrue(tomllib.loads(config_path.read_text())["plugins"]["video-clipper@video-clipper"]["enabled"])

    def test_upgrade_recognizes_legacy_windows_marketplace_path(self):
        from tools.install_plugin import codex_config

        with tempfile.TemporaryDirectory(dir=clipping.ROOT / "temp") as directory:
            root = Path(directory)
            marketplace = root / "cache"
            marketplace.mkdir()
            config = root / "config.toml"
            legacy = chr(92) * 2 + "?" + chr(92) + str(marketplace.resolve())
            original = f"[marketplaces.video-clipper]\nsource_type = \"local\"\nsource = '{legacy}'\n"
            config.write_bytes(original.encode("utf-8"))
            updated = codex_config(config, marketplace)
            self.assertTrue(updated.startswith(original))
            self.assertEqual(updated.count("[marketplaces.video-clipper]"), 1)

    def test_failed_plugin_switch_restores_previous_tree(self):
        from tools.install_plugin import install_tree

        with tempfile.TemporaryDirectory(dir=clipping.ROOT / "temp") as directory:
            root = Path(directory)
            dest = root / "plugins/video-clipper"
            dest.mkdir(parents=True)
            (dest / "README.md").write_text("previous revision")
            rename = Path.rename

            def fail_switch(path, target):
                if path.parent.name.startswith(".clip-install-"):
                    raise OSError("simulated switch failure")
                return rename(path, target)

            with patch.object(Path, "rename", fail_switch), self.assertRaises(OSError):
                install_tree(dest, ".codex-plugin", root / "backups")
            self.assertEqual((dest / "README.md").read_text(), "previous revision")


if __name__ == '__main__':
    unittest.main(verbosity=2)
