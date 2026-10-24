import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ip_location import (  # noqa: E402
    IpLocator,
    database_file_info,
    format_region,
    lookup_ip_location,
    normalize_region_name,
    parse_region,
)


class FormatRegionTestCase(unittest.TestCase):
    def test_chinese_province_and_city(self):
        self.assertEqual(
            format_region("中国|广东省|深圳市|电信|CN"),
            "中国 · 广东省 · 深圳市（电信）",
        )

    def test_municipality_is_not_repeated(self):
        self.assertEqual(
            format_region("中国|北京市|北京市|联通|CN"),
            "中国 · 北京市（联通）",
        )

    def test_foreign_country_uses_chinese_name(self):
        self.assertEqual(
            format_region("United States|California|Mountain View|Google LLC|US"),
            "美国 · California · Mountain View（Google LLC）",
        )

    def test_reserved_address(self):
        self.assertEqual(
            format_region("Reserved|Reserved|Reserved|0|0"),
            "保留地址",
        )

    def test_missing_fields(self):
        self.assertEqual(format_region("中国|0|0|0|CN"), "中国")
        self.assertEqual(format_region(""), "归属地未知")
        self.assertEqual(format_region("0|0|0|0|0"), "归属地未知")

    def test_parse_region(self):
        region = parse_region("中国|浙江省|杭州市|阿里|CN")
        self.assertIsNotNone(region)
        self.assertEqual(region.country, "中国")
        self.assertEqual(region.province, "浙江省")
        self.assertEqual(region.city, "杭州市")
        self.assertEqual(region.isp, "阿里")
        self.assertEqual(region.iso, "CN")

    def test_normalize_region_name(self):
        self.assertEqual(normalize_region_name("浙江省"), "浙江")
        self.assertEqual(normalize_region_name("杭州市"), "杭州")
        self.assertEqual(normalize_region_name("香港特别行政区"), "香港")
        self.assertEqual(normalize_region_name("北京市"), "北京")
        self.assertEqual(normalize_region_name("新疆"), "新疆")
        self.assertEqual(normalize_region_name("0"), "")


class LocatorTestCase(unittest.TestCase):
    def test_private_and_invalid_addresses(self):
        with tempfile.TemporaryDirectory() as directory:
            locator = IpLocator(directory)
            self.assertEqual(locator.lookup("127.0.0.1"), "本地或内网地址")
            self.assertEqual(locator.lookup("192.168.1.8"), "本地或内网地址")
            self.assertEqual(locator.lookup("::1"), "本地或内网地址")
            self.assertEqual(locator.lookup("not-an-ip"), "未知 IP")

    def test_missing_database_degrades_gracefully(self):
        with tempfile.TemporaryDirectory() as directory:
            locator = IpLocator(directory)
            self.assertEqual(locator.lookup("8.8.8.8"), "归属地未知")
            self.assertEqual(
                locator.lookup("2001:4860:4860::8888"),
                "IPv6 地址（暂无归属地数据）",
            )
            self.assertIsNone(locator.parts("8.8.8.8"))
            self.assertEqual(locator.file_info(), [])

    def test_real_database_when_installed(self):
        info = database_file_info()
        if not info:
            self.skipTest("ip2region xdb 数据文件未安装")

        v4 = next((item for item in info if item["version"] == 4), None)
        self.assertIsNotNone(v4)
        self.assertEqual(len(v4["sha256"]), 64)
        self.assertGreater(v4["size"], 0)
        self.assertRegex(v4["build_date"], r"^\d{4}-\d{2}-\d{2}$")

        location = lookup_ip_location("223.5.5.5")
        self.assertIn("中国", location)
        self.assertIn("浙江", location)


if __name__ == "__main__":
    unittest.main()
