"""Create a small, deterministic buggy project for the two-minute demo."""

from __future__ import annotations

import argparse
from pathlib import Path


TASK = """# 演示任务：修复原子库存预留

`inventory.py` 的 `Inventory.reserve()` 接收 `{商品: 数量}` 请求。

验收要求：

1. 只有所有商品都存在、数量都是正整数且库存都充足时才扣减。
2. 任意一项失败时，整个请求不得改变任何库存（all-or-nothing）。
3. `bool` 不是合法数量，即使 Python 中 `bool` 是 `int` 的子类。
4. 不要修改测试；运行 `python -m unittest -v` 验证全部行为。
5. 保持实现简洁，并在完成后说明根因和验证结果。
"""


INVENTORY = '''"""A deliberately buggy inventory implementation for the Forge demo."""


class Inventory:
    def __init__(self, stock):
        self.stock = dict(stock)

    def reserve(self, request):
        """Reserve requested items and return the remaining stock."""
        if not isinstance(request, dict) or not request:
            raise ValueError("request must be a non-empty mapping")

        # BUG: validation and mutation are interleaved, so a later failure can
        # leave earlier items deducted. bool also passes isinstance(..., int).
        for sku, quantity in request.items():
            if sku not in self.stock:
                raise KeyError(sku)
            if not isinstance(quantity, int) or quantity <= 0:
                raise ValueError("quantity must be a positive integer")
            if self.stock[sku] < quantity:
                raise ValueError(f"insufficient stock for {sku}")
            self.stock[sku] -= quantity

        return dict(self.stock)
'''


TESTS = '''import unittest

from inventory import Inventory


class InventoryTests(unittest.TestCase):
    def test_successful_multi_item_reservation(self):
        inventory = Inventory({"apple": 5, "banana": 4})
        remaining = inventory.reserve({"apple": 2, "banana": 1})
        self.assertEqual(remaining, {"apple": 3, "banana": 3})
        self.assertEqual(inventory.stock, remaining)

    def test_insufficient_stock_is_atomic(self):
        inventory = Inventory({"apple": 5, "banana": 1})
        before = dict(inventory.stock)
        with self.assertRaises(ValueError):
            inventory.reserve({"apple": 2, "banana": 3})
        self.assertEqual(inventory.stock, before)

    def test_unknown_item_is_atomic(self):
        inventory = Inventory({"apple": 5})
        before = dict(inventory.stock)
        with self.assertRaises(KeyError):
            inventory.reserve({"apple": 2, "pear": 1})
        self.assertEqual(inventory.stock, before)

    def test_invalid_quantities_do_not_mutate_stock(self):
        for quantity in (0, -1, 1.5, True):
            with self.subTest(quantity=quantity):
                inventory = Inventory({"apple": 5})
                before = dict(inventory.stock)
                with self.assertRaises((TypeError, ValueError)):
                    inventory.reserve({"apple": quantity})
                self.assertEqual(inventory.stock, before)


if __name__ == "__main__":
    unittest.main()
'''


def prepare(force: bool = False) -> Path:
    root = Path(__file__).resolve().parent.parent
    target = root / ".forge" / "video-demo"
    files = {
        "TASK.md": TASK,
        "inventory.py": INVENTORY,
        "test_inventory.py": TESTS,
    }
    existing = [name for name in files if (target / name).exists()]
    if existing and not force:
        names = ", ".join(existing)
        raise ValueError(
            f"demo already exists ({names}); pass --force to restore the buggy baseline"
        )
    target.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (target / name).write_text(content, encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force", action="store_true", help="overwrite demo files with the buggy baseline"
    )
    args = parser.parse_args()
    try:
        target = prepare(force=args.force)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f"demo workspace: {target}")
    print("expected baseline: 4 tests, 3 failures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
