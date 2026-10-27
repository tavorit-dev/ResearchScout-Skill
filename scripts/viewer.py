"""Static HTML viewer. Deferred to Phase 5 (spec 4.2)."""


def build(root):
    """Not implemented in v1.

    The archive layout already reserves ``reports/`` and ``index/`` for the
    static viewer (spec 4.2, Phase 5), so the interface exists while the
    implementation does not. Nothing is read from ``root``: a placeholder that
    touched the archive could make an unimplemented feature look partially
    supported.
    """
    raise NotImplementedError(
        "静态 HTML 查看器属于 Phase 5，v1 未实现。归档结构已为它保留接口。"
    )
