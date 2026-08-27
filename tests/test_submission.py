import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.package_submission import (
    SubmissionError,
    build_submission,
    chinese_character_count,
    validate_readme,
)


class SubmissionPackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.readme = self.root / "README.txt"
        self.readme.write_text(
            "项目说明\n公开仓库：https://github.com/example/forge-agent\n",
            encoding="utf-8",
        )
        self.video = self.root / "demo.mp4"
        self.video.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32)

    def tearDown(self):
        self.temporary.cleanup()

    def test_builds_exact_two_file_archive(self):
        target = build_submission(
            name="张三",
            video=self.video,
            readme=self.readme,
            output_directory=self.root / "out",
            check_duration=False,
        )
        self.assertEqual(target.name, "张三.zip")
        with zipfile.ZipFile(target) as archive:
            self.assertEqual(archive.namelist(), ["README.txt", "demo.mp4"])

    def test_placeholder_repository_is_rejected(self):
        self.readme.write_text(
            "公开 Git 仓库：[发布后在此填写 GitHub/Gitee 地址]", encoding="utf-8"
        )
        with self.assertRaisesRegex(SubmissionError, "public GitHub/Gitee"):
            validate_readme(self.readme)

    def test_refuses_to_overwrite_archive(self):
        kwargs = {
            "name": "张三",
            "video": self.video,
            "readme": self.readme,
            "output_directory": self.root,
            "check_duration": False,
        }
        build_submission(**kwargs)
        with self.assertRaisesRegex(SubmissionError, "overwrite"):
            build_submission(**kwargs)

    def test_chinese_character_counter(self):
        self.assertEqual(chinese_character_count("Forge 编程 Agent 智能体"), 5)


if __name__ == "__main__":
    unittest.main()
