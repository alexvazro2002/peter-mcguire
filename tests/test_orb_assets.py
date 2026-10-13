"""All upstream animation frames, including loop seams, remain within the bitmap."""
import json
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]


def test_animated_orbs_have_clear_edges_across_entire_loop():
    for theme in ('dark', 'light'):
        for state in ('breathing', 'listening', 'working', 'speaking'):
            with Image.open(ROOT / 'assets/thinking-orbs' / f'{theme}-{state}.webp') as image:
                assert image.n_frames == 144
                first = image.convert('RGBA').copy()
                for index in range(image.n_frames):
                    image.seek(index)
                    bounds = image.convert('RGBA').getchannel('A').getbbox()
                    assert bounds and bounds[0] >= 8 and bounds[1] >= 8
                    assert bounds[2] <= image.width - 8 and bounds[3] <= image.height - 8
                assert ImageChops.difference(first, image.convert('RGBA')).getbbox() is None


def test_standby_input_and_output_have_distinct_upstream_timelines():
    states = json.loads((ROOT / 'assets/thinking-orbs/provenance.json').read_text())['states']
    assert states['breathing']['sourceState'] == 'breathing'
    assert states['working']['sourceState'] == 'working'
    assert states['listening']['sourceState'] == states['speaking']['sourceState'] == 'listening'
    assert states['breathing']['speed'] < states['listening']['speed'] < states['speaking']['speed']
