from __future__ import annotations

from builders import apple_aux_xmp, hdrgm_xmp

from gainmap_audit.xmp import (
    APDI_NS,
    CONTAINER_NS,
    HDRGAINMAP_NS,
    HDRGM_NS,
    ITEM_NS,
    Xmp,
)


def test_declares_and_get_attribute_style():
    x = Xmp(hdrgm_xmp(version="1.0"))
    assert x.declares(HDRGM_NS)
    assert x.get(HDRGM_NS, "Version") == "1.0"
    assert x.has(HDRGM_NS, "Version")


def test_undeclared_namespace_is_absent():
    x = Xmp(hdrgm_xmp())
    assert not x.declares(APDI_NS)
    assert x.get(APDI_NS, "AuxiliaryImageType") is None


def test_apple_aux_attribute_lookup():
    x = Xmp(apple_aux_xmp(version="131072"))
    assert x.get(APDI_NS, "AuxiliaryImageType") == "urn:com:apple:photo:2020:aux:hdrgainmap"
    assert x.get(HDRGAINMAP_NS, "HDRGainMapVersion") == "131072"


def test_element_style_property_also_matches():
    text = (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description xmlns:hdrgm="http://ns.adobe.com/hdr-gain-map/1.0/">'
        "<hdrgm:Version>1.0</hdrgm:Version>"
        "</rdf:Description></rdf:RDF></x:xmpmeta>"
    )
    x = Xmp(text)
    assert x.get(HDRGM_NS, "Version") == "1.0"


def test_fallback_prefix_used_when_xmlns_declaration_missing():
    # A writer that emits hdrgm:Version without declaring the xmlns still
    # resolves via the well-known default prefix.
    text = '<rdf:Description hdrgm:Version="1.0"></rdf:Description>'
    x = Xmp(text)
    assert not x.declares(HDRGM_NS)
    assert x.get(HDRGM_NS, "Version") == "1.0"


def test_prefix_that_is_a_suffix_of_another_prefix_is_not_mismatched():
    # A packet that declares an unrelated "xhdrgm" namespace and writes
    # xhdrgm:Version must not satisfy a lookup for the real hdrgm:Version,
    # even though "hdrgm:Version" is textually a substring of the former.
    text = (
        '<rdf:Description xmlns:xhdrgm="http://example.com/unrelated/1.0/" '
        'xhdrgm:Version="99"></rdf:Description>'
    )
    x = Xmp(text)
    assert not x.declares(HDRGM_NS)
    assert x.get(HDRGM_NS, "Version") is None


def test_default_prefix_is_not_used_as_fallback_when_claimed_by_another_namespace():
    # "hdrgm" is only the conventional prefix by convention. If this packet
    # bound it to a totally unrelated namespace instead, and never declared
    # the real HDRGM_NS at all, an hdrgm:Version attribute belongs to that
    # other namespace and must not be read as the Adobe gain map property.
    text = (
        '<rdf:Description xmlns:hdrgm="http://example.com/unrelated-vendor/1.0/" '
        'hdrgm:Version="99"></rdf:Description>'
    )
    x = Xmp(text)
    assert not x.declares(HDRGM_NS)
    assert x.prefixes_for(HDRGM_NS) == []
    assert x.get(HDRGM_NS, "Version") is None


def test_custom_prefix_binding_is_honored():
    text = (
        '<rdf:Description xmlns:gainmap="http://ns.adobe.com/hdr-gain-map/1.0/" '
        'gainmap:Version="1.0"></rdf:Description>'
    )
    x = Xmp(text)
    # The declared prefix is tried first; the well-known default is also
    # kept as a fallback in case the packet mixes both spellings.
    assert x.prefixes_for(HDRGM_NS) == ["gainmap", "hdrgm"]
    assert x.get(HDRGM_NS, "Version") == "1.0"


def test_container_items_with_container_item_wrapper():
    x = Xmp(hdrgm_xmp(with_container=True))
    items = x.container_items()
    assert [i.semantic for i in items] == ["Primary", "GainMap"]
    gain_map = x.gain_map_item()
    assert gain_map is not None
    assert gain_map.mime == "image/jpeg"
    assert gain_map.length == 1234


def test_container_items_bare_rdf_li():
    text = (
        '<rdf:Description xmlns:Container="http://ns.google.com/photos/1.0/container/" '
        'xmlns:Item="http://ns.google.com/photos/1.0/container/item/">'
        "<Container:Directory><rdf:Seq>"
        '<rdf:li Item:Semantic="Primary" Item:Mime="image/jpeg"/>'
        '<rdf:li Item:Semantic="GainMap" Item:Mime="image/jpeg" Item:Length="42"/>'
        "</rdf:Seq></Container:Directory>"
        "</rdf:Description>"
    )
    x = Xmp(text)
    items = x.container_items()
    assert [i.semantic for i in items] == ["Primary", "GainMap"]
    assert items[1].length == 42


def test_no_container_directory_returns_empty():
    x = Xmp(hdrgm_xmp(with_container=False))
    assert x.container_items() == ()
    assert x.gain_map_item() is None


def test_truncated_packet_does_not_raise():
    x = Xmp(hdrgm_xmp(with_container=True)[:80])
    assert x.get(HDRGM_NS, "Version") is None or isinstance(x.get(HDRGM_NS, "Version"), str)
    assert isinstance(x.container_items(), tuple)


def test_container_ns_and_item_ns_distinct_prefixes():
    x = Xmp(hdrgm_xmp(with_container=True))
    assert x.prefixes_for(CONTAINER_NS) == ["Container"]
    assert x.prefixes_for(ITEM_NS) == ["Item"]
