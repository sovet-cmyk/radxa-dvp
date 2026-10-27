import json
from pathlib import Path
import tempfile
import unittest

from touch_sender import CoordinateMapper,SenderConfig


class ResolutionIndependentTouchTests(unittest.TestCase):
    def test_normalized_touch_maps_to_both_supported_displays(self):
        mapper=CoordinateMapper(0,4095,0,4095)
        x,y=mapper.map(3071.25,1023.75)
        self.assertAlmostEqual(x,0.75,places=5)
        self.assertAlmostEqual(y,0.25,places=5)
        self.assertEqual((round(x*799),round(y*479)),(599,120))
        self.assertEqual((round(x*1919),round(y*1079)),(1439,270))

    def test_rotation_is_applied_before_normalized_transmission(self):
        mapper=CoordinateMapper(0,100,0,100,rotation=90)
        self.assertEqual(mapper.map(25,75),(0.25,0.25))

    def test_local_pairing_overrides_default_touch_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            config=root/'config.json'
            config.write_text(json.dumps({
                'shared_key':'original-key-at-least-16',
            }),encoding='utf-8')
            (root/'pairing.json').write_text(json.dumps({
                'unit_id':'1',
                'ssid':'DVP-TOUCH1',
                'wifi_password':'password-at-least-8',
                'touch_shared_key':'paired-key-at-least-16',
            }),encoding='utf-8')
            self.assertEqual(SenderConfig.load(config).shared_key,
                             'paired-key-at-least-16')


if __name__=='__main__':
    unittest.main()
