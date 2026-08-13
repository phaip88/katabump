import unittest
from urllib.parse import parse_qs, urlparse

from proxy_handler import normalize_ws_path_and_early_data, parse_vless


class VlessWebSocketParsingTests(unittest.TestCase):
    def parse(self, url):
        parsed = urlparse(url)
        return parse_vless(parsed, parse_qs(parsed.query))

    def test_embedded_early_data_is_converted(self):
        outbound = self.parse(
            "vless://00000000-0000-0000-0000-000000000000@example.com:443"
            "?security=tls&type=ws&host=edge.example.com"
            "&path=%2Fvless-argo%3Fed%3D2560"
        )
        transport = outbound["transport"]
        self.assertEqual(transport["path"], "/vless-argo")
        self.assertEqual(transport["max_early_data"], 2560)
        self.assertEqual(
            transport["early_data_header_name"], "Sec-WebSocket-Protocol"
        )

    def test_top_level_early_data_is_converted(self):
        outbound = self.parse(
            "vless://00000000-0000-0000-0000-000000000000@example.com:443"
            "?security=tls&type=ws&path=%2Fws&ed=2048&eh=X-Early-Data"
        )
        transport = outbound["transport"]
        self.assertEqual(transport["path"], "/ws")
        self.assertEqual(transport["max_early_data"], 2048)
        self.assertEqual(transport["early_data_header_name"], "X-Early-Data")

    def test_query_parameters_other_than_ed_are_preserved(self):
        path, early_data, header = normalize_ws_path_and_early_data(
            "/ws?foo=bar&ed=1024&token=abc", {}
        )
        self.assertEqual(path, "/ws?foo=bar&token=abc")
        self.assertEqual(early_data, 1024)
        self.assertEqual(header, "Sec-WebSocket-Protocol")

    def test_missing_leading_slash_is_normalized(self):
        path, early_data, _ = normalize_ws_path_and_early_data("ws", {})
        self.assertEqual(path, "/ws")
        self.assertEqual(early_data, 0)

    def test_invalid_early_data_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Invalid VLESS"):
            normalize_ws_path_and_early_data("/ws?ed=invalid", {})


if __name__ == "__main__":
    unittest.main()
