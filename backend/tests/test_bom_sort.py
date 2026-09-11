"""BOM siralama operasyon sirasi ile uyumlu olmali."""

from app.services.bom_tree import bom_line_sort_key, is_wip_asm_link, sort_bom_lines_for_display


class _Bl:
    def __init__(self, code: str, src: str, branch_sira: int, recipe_seq: int):
        self.component_code = code
        self.source_wip = src
        self.branch_listing_sira = branch_sira
        self.recipe_seq = recipe_seq


def test_bom_sort_branch_then_operation():
    lines = [
        _Bl("5001848-15", "5001848-15", 50, 40),
        _Bl("5001848-23", "5001848-15", 50, 10),
        _Bl("5001848-15", "5001848-15", 50, 0),
        _Bl("5600011-48", "5600011-48", 30, 0),
        _Bl("5600011-32", "5600011-48", 30, 10),
    ]
    out = sort_bom_lines_for_display(lines)
    codes = [b.component_code for b in out]
    assert codes.index("5001848-15") < codes.index("5001848-23")
    assert codes.index("5600011-48") < codes.index("5600011-32")
    assert codes.index("5001848-23") < codes.index("5600011-48")


def test_asm_link_recipe_seq_zero_first_in_branch():
    lines = [_Bl("5001848-23", "5001848-15", 50, 10), _Bl("5001848-15", "5001848-15", 50, 0)]
    out = sort_bom_lines_for_display(lines)
    assert out[0].component_code == "5001848-15"
    assert is_wip_asm_link(out[0].component_code, out[0].source_wip, out[0].recipe_seq)
